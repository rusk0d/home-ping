package com.david.homeenv

import android.app.TimePickerDialog
import android.content.DialogInterface
import android.os.Bundle
import android.widget.CompoundButton
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import com.david.homeenv.databinding.ActivityNotificationSettingsBinding
import com.david.homeenv.ui.Levels
import com.google.android.material.snackbar.Snackbar
import com.google.android.material.switchmaterial.SwitchMaterial
import org.json.JSONObject
import kotlin.concurrent.thread

/**
 * Which alerts push, and quiet hours (#82). The settings live on the server
 * (`/api/notification_settings`, contracts/api.md), so they apply to every phone.
 * Each change is sent on its own; on failure the control goes back and a Snackbar says so.
 */
class NotificationSettingsActivity : AppCompatActivity() {

    private lateinit var b: ActivityNotificationSettingsBinding
    // Last state the server confirmed; the controls are reset to it after a failed change
    private var settings: JSONObject? = null

    private val kindSwitches by lazy {
        mapOf("humidity" to b.humidity, "temperature" to b.temperature, "radar" to b.radar, "nodes" to b.nodes)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        b = ActivityNotificationSettingsBinding.inflate(layoutInflater)
        setContentView(b.root)
        b.back.setOnClickListener { finish() }
        b.quietStartRow.setOnClickListener { pickTime("start", b.quietStart) }
        b.quietEndRow.setOnClickListener { pickTime("end", b.quietEnd) }
        load()
    }

    private fun load() {
        thread {
            val s = Api.getObject("/api/notification_settings")
            runOnUiThread {
                if (isDestroyed) return@runOnUiThread
                if (s == null) {
                    b.status.text = "Can't reach the server. Settings can't be changed right now."
                } else {
                    b.status.text = "Changes apply right away."
                    show(s)
                }
            }
        }
    }

    /** Shows [s] (the server's settings) and enables the controls. */
    private fun show(s: JSONObject) {
        settings = s
        kindSwitches.forEach { (kind, sw) ->
            setSilently(sw, s.optBoolean(kind, true)) { checked -> save(JSONObject().put(kind, checked)) }
        }
        val quiet = s.optJSONObject("quiet_hours") ?: JSONObject()
        setSilently(b.quietEnabled, quiet.optBoolean("enabled")) { checked ->
            save(JSONObject().put("quiet_hours", JSONObject().put("enabled", checked)))
        }
        b.quietStart.text = quiet.optString("start", "22:00")
        b.quietEnd.text = quiet.optString("end", "07:00")
        b.quietStartRow.isEnabled = true
        b.quietEndRow.isEnabled = true
    }

    private fun setSilently(sw: SwitchMaterial, checked: Boolean, onChange: (Boolean) -> Unit) {
        sw.setOnCheckedChangeListener(null)
        sw.isChecked = checked
        sw.isEnabled = true
        sw.setOnCheckedChangeListener { _: CompoundButton, isChecked -> onChange(isChecked) }
    }

    private fun pickTime(field: String, label: TextView) {
        if (settings == null) return
        val (h, m) = label.text.split(":").map { it.toIntOrNull() ?: 0 }.let { it[0] to it.getOrElse(1) { 0 } }
        val dialog = TimePickerDialog(this, { _, hour, minute ->
            val hhmm = "%02d:%02d".format(hour, minute)
            label.text = hhmm
            save(JSONObject().put("quiet_hours", JSONObject().put(field, hhmm)))
        }, h, m, true)
        dialog.show()
        // The theme's colorPrimary is the card colour, which would make the buttons invisible
        val accent = Levels.color(this, R.color.good)
        dialog.getButton(DialogInterface.BUTTON_POSITIVE)?.setTextColor(accent)
        dialog.getButton(DialogInterface.BUTTON_NEGATIVE)?.setTextColor(accent)
    }

    /** POSTs a partial change; the response is the full new state. */
    private fun save(change: JSONObject) {
        thread {
            val updated = Api.postForObject("/api/notification_settings", change)
            runOnUiThread {
                if (isDestroyed) return@runOnUiThread
                if (updated != null) {
                    show(updated)
                } else {
                    settings?.let { show(it) }
                    Snackbar.make(b.root, "Couldn't save. Check the connection.", Snackbar.LENGTH_LONG).show()
                }
            }
        }
    }
}
