"""Fridge temperature monitor alerts (#290)."""

import logging

import alerts
from store import db, get_state, set_state

log = logging.getLogger(__name__)

# Alert state tracking: fridge_alert_state = "" | "cold" | "warm_low" | "warm_critical" | "warm_critical_duration"
# Cleared when alert state from sensor goes back to ""


def check_fridge_alerts():
	"""Check latest fridge reading and send alerts on state change."""
	with db() as conn:
		c = conn.cursor()
		c.execute("""
			SELECT alert FROM readings WHERE device = 'fridge_temp_esp32'
			ORDER BY id DESC LIMIT 1
		""")
		row = c.fetchone()

	if not row:
		return

	current_alert = row[0] or ""
	last_state = get_state("fridge_alert_state", "")

	if current_alert == last_state:
		return  # No state change

	set_state("fridge_alert_state", current_alert)

	# Alert messages for each state
	alert_msgs = {
		"cold": ("Fridge too cold", "Temperature below 0°C for 30+ minutes"),
		"warm_low": ("Fridge warming up", "Temperature above 5°C for 30+ minutes"),
		"warm_critical": ("Fridge too warm", "Temperature above 8°C for 30+ minutes"),
		"warm_critical_duration": ("Fridge too warm (sustained)", "Temperature above 5°C for 2+ hours"),
	}

	if current_alert == "":
		# Back to normal: send recovery notification
		if last_state:  # Only notify if we were previously alerted
			title = "Fridge back to normal"
			body = "Temperature is within normal range"
			alerts.alert(
				device="fridge_temp_esp32",
				event_type="FRIDGE_ALERT_RECOVERY",
				detail=f"Fridge back to normal (was: {last_state})",
				title=title,
				body=body,
				data={"event": "FRIDGE_NORMAL", "device": "fridge_temp_esp32"},
				cooldown_key="fridge_temp",
				kind="fridge"
			)
	else:
		# Alert started or changed
		title, body = alert_msgs.get(current_alert, ("Fridge alert", "Check temperature"))
		alerts.alert(
			device="fridge_temp_esp32",
			event_type="FRIDGE_ALERT",
			detail=f"Fridge alert: {current_alert}",
			title=title,
			body=body,
			data={"event": "FRIDGE_ALERT", "device": "fridge_temp_esp32", "alert": current_alert},
			cooldown_key="fridge_temp",
			kind="fridge"
		)


def send_fridge_alerts():
	"""Wrapper to match server_checks pattern."""
	try:
		check_fridge_alerts()
	except Exception as e:
		log.error("Fridge alert check failed: %s", e)
