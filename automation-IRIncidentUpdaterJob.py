"""
IR Incident Updater Job — Scheduled Job Script

Runs on a cron schedule (e.g. every 15 minutes). Queries active IR incidents
that are due for a status update, sends the queued update text to the IR Zoom
channel, archives the sent update to the history grid, clears the update field,
and advances the next update time based on cadence.

Author: Automation Engineering
"""

from CommonServerPython import *
import json
import traceback
from datetime import datetime, timedelta

SEVERITY_CADENCE = {
    4: 'Every 4h',   # Critical
    3: 'Every 8h',   # High
    2: 'Daily 5pm',  # Medium
    1: 'Daily 5pm',  # Low
}

CADENCE_DELTAS = {
    'Hourly': timedelta(hours=1),
    'Every 4h': timedelta(hours=4),
    'Every 8h': timedelta(hours=8),
    'Every 24h': timedelta(hours=24),
}


def parse_dt(dt_str):
    if not dt_str:
        return None
    try:
        return datetime.strptime(dt_str.split('.')[0].split('+')[0].replace('Z', ''), '%Y-%m-%dT%H:%M:%S')
    except Exception:
        return None


def get_severity_name(severity):
    return {0: 'Unknown', 1: 'Low', 2: 'Medium', 3: 'High', 4: 'Critical'}.get(severity, 'Unknown')


def compute_next_update_time(cadence, now):
    """Compute the next update time based on cadence string.
    For 'Daily Xpm/am' cadences, compute next occurrence of that time.
    For interval cadences, add the interval to now."""
    if cadence in CADENCE_DELTAS:
        return now + CADENCE_DELTAS[cadence]

    # Handle "Daily 5pm", "Daily 9am" etc.
    if cadence and cadence.lower().startswith('daily'):
        time_part = cadence.split(' ', 1)[-1].strip().lower()
        hour = 17  # default 5pm
        if time_part.endswith('am'):
            hour = int(time_part.replace('am', '').strip())
        elif time_part.endswith('pm'):
            h = int(time_part.replace('pm', '').strip())
            hour = h if h == 12 else h + 12

        # Next occurrence of that hour (ET approximation: UTC-4 / UTC-5)
        # Store as UTC. 5pm ET ≈ 21:00 UTC (EDT) or 22:00 UTC (EST)
        # Using 21:00 UTC as default (EDT)
        target_utc_hour = hour + 4  # ET offset approximation
        if target_utc_hour >= 24:
            target_utc_hour -= 24

        next_time = now.replace(hour=target_utc_hour, minute=0, second=0, microsecond=0)
        if next_time <= now:
            next_time += timedelta(days=1)
        return next_time

    # Fallback: 24 hours
    return now + timedelta(hours=24)


def format_update_message(incident, update_text):
    """Compose the formatted update message to send to the Zoom channel."""
    inc_id = incident.get('id', '')
    inc_name = incident.get('name', '')
    severity = incident.get('severity', 0)
    severity_name = get_severity_name(severity)
    custom_fields = incident.get('CustomFields', {})

    # Determine current phase from containment booleans
    phase = 'Active Response'
    if custom_fields.get('isverifiedrecovery'):
        phase = 'Recovery Verified'
    elif custom_fields.get('iseradicated'):
        phase = 'Eradicated'
    elif custom_fields.get('iscontained'):
        phase = 'Contained'

    now_str = datetime.utcnow().strftime('%b %d, %Y %H:%M UTC')

    message = (
        f"--- Incident Update ---\n"
        f"Incident: #{inc_id} — {inc_name}\n"
        f"Severity: {severity_name} | Phase: {phase}\n"
        f"Posted: {now_str}\n"
        f"---\n\n"
        f"{update_text}"
    )
    return message


def send_update(incident, update_text, zoom_instance, user_id):
    """Send the update message to the IR Zoom channel."""
    custom_fields = incident.get('CustomFields', {})
    channel_id = custom_fields.get('zoomchannelid', '')
    if not channel_id:
        demisto.debug(f'IRUpdaterJob: No channel ID for incident {incident.get("id")}')
        return False

    message = format_update_message(incident, update_text)

    cmd_args = {
        'user_id': user_id,
        'to_channel': channel_id,
        'message': message,
    }
    if zoom_instance:
        cmd_args['using'] = zoom_instance

    res = demisto.executeCommand('zoom-send-message', cmd_args)
    if is_error(res):
        demisto.error(f'IRUpdaterJob: zoom-send-message failed for incident {incident.get("id")}: {get_error(res)}')
        return False
    return True


def archive_and_advance(incident_id, update_text, cadence, severity):
    """Archive the sent update to history grid, clear update field, and set next update time."""
    now = datetime.utcnow()
    now_iso = now.strftime('%Y-%m-%dT%H:%M:%SZ')

    # Resolve effective cadence
    effective_cadence = cadence or SEVERITY_CADENCE.get(severity, 'Daily 5pm')
    next_time = compute_next_update_time(effective_cadence, now)
    next_iso = next_time.strftime('%Y-%m-%dT%H:%M:%SZ')

    # Update incident fields: clear update text, set timestamps, advance timer
    demisto.executeCommand('setIncident', {
        'id': incident_id,
        'customFields': json.dumps({
            'irupdatetext': '',
            'irlastupdateposted': now_iso,
            'irnextupdatetime': next_iso,
        }),
    })

    # Append to history grid via SetGridField
    history_row = {
        'timestamp': now_iso,
        'updatetext': update_text[:2000],  # Truncate to avoid oversized grid entries
    }
    demisto.executeCommand('SetGridField', {
        'id': incident_id,
        'val': json.dumps([history_row]),
        'gridid': 'irupdatehistory',
        'overwrite': 'false',
    })


def main():
    try:
        args = demisto.args()
        zoom_instance = args.get('zoom_instance', '') or None
        user_id = args.get('user_id', '')

        if not user_id:
            raise DemistoException('user_id argument is required')

        now = datetime.utcnow()
        now_iso = now.strftime('%Y-%m-%dT%H:%M:%SZ')

        # Query active IR incidents due for update
        query = (
            'type:"IR Incident" AND status:Active '
            'AND zoomchannelid:* '
            f'AND irnextupdatetime:<"{now_iso}"'
        )

        res = demisto.executeCommand('getIncidents', {
            'query': query,
            'size': 100,
        })
        if is_error(res):
            raise DemistoException(f'getIncidents failed: {get_error(res)}')

        incidents_data = res[0].get('Contents', {}).get('data', [])
        if not incidents_data:
            demisto.debug('IRUpdaterJob: No incidents due for update')
            return_results('No incidents due for update.')
            return

        sent_count = 0
        skipped_count = 0

        for inc in incidents_data:
            inc_id = inc.get('id', '')
            custom_fields = inc.get('CustomFields', {})
            update_text = custom_fields.get('irupdatetext', '')
            cadence = custom_fields.get('irupdatercadence', '')
            severity = inc.get('severity', 0)

            if not update_text or not update_text.strip():
                demisto.debug(f'IRUpdaterJob: No update text for incident {inc_id}, skipping')
                skipped_count += 1
                # Still advance the timer so it doesn't fire every cycle
                effective_cadence = cadence or SEVERITY_CADENCE.get(severity, 'Daily 5pm')
                next_time = compute_next_update_time(effective_cadence, now)
                demisto.executeCommand('setIncident', {
                    'id': inc_id,
                    'customFields': json.dumps({
                        'irnextupdatetime': next_time.strftime('%Y-%m-%dT%H:%M:%SZ'),
                    }),
                })
                continue

            success = send_update(inc, update_text, zoom_instance, user_id)
            if success:
                archive_and_advance(inc_id, update_text, cadence, severity)
                sent_count += 1
                demisto.debug(f'IRUpdaterJob: Update sent for incident {inc_id}')
            else:
                skipped_count += 1

        return_results(f'Incident Updater Job complete. Sent: {sent_count}, Skipped: {skipped_count}')

    except Exception as e:
        demisto.error(f'IRUpdaterJob failed: {traceback.format_exc()}')
        return_error(f'IR Incident Updater Job failed: {str(e)}')


if __name__ in ('__main__', '__builtin__', 'builtins'):
    main()
