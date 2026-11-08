package com.david.homeenv

import org.json.JSONArray
import org.json.JSONObject

/**
 * What needs a look on the Devices screen (#325): the red dot on the header's devices button
 * and the list at the top of the screen. From /api/devices (`online`, `health`, #322),
 * /api/restarts and /api/server_status (#260; the restore drill since #340); a missing part adds nothing.
 */
object DeviceIssues {
    /** WiFi below this (dBm) drops and reconnects often: the "poor" band on the Devices screen. */
    const val WEAK_RSSI = -80
    /** More restarts than this in 24 h is worth a look even without a crash reason. */
    const val MAX_RESTARTS_24H = 2

    fun list(devices: JSONArray?, restarts: JSONObject?, server: JSONObject?): List<String> {
        val out = mutableListOf<String>()
        // Only the nodes in the backend's registry: a retired one stays listed for 30 days (#359)
        for (d in Device.list(devices)) {
            if (!d.online && d.known) out += "${d.name} is offline"
        }
        for (i in 0 until (devices?.length() ?: 0)) {
            val d = devices?.optJSONObject(i) ?: continue
            val name = d.optString("name").ifEmpty { d.optString("device") }
            d.optJSONObject("health")?.optNum("rssi")?.toInt()?.takeIf { it < WEAK_RSSI }?.let {
                out += "$name: weak WiFi ($it dBm)"
            }
        }
        restarts?.keys()?.forEach { id ->
            val r = restarts.optJSONObject(id) ?: return@forEach
            val name = Device.list(devices).firstOrNull { it.id == id }?.name ?: id
            val day = r.optInt("last_24h")
            val reason = r.takeUnless { it.isNull("last_reason") }?.optString("last_reason")
            when {
                day > MAX_RESTARTS_24H -> out += "$name: $day restarts in 24 h"
                day > 0 && reason != null && HomeFormat.BAD_RESET_REASONS.any { reason.contains(it) } ->
                    out += "$name: $reason reset"
            }
        }
        server?.let { s ->
            s.optJSONObject("backend")?.takeIf { !it.optBoolean("mqtt_connected", true) }?.let { out += "Backend: MQTT disconnected" }
            if (s.failed("backup")) out += "Backup overdue"
            if (s.failed("offsite")) out += "S3 copy overdue"
            s.optJSONObject("drill")?.takeIf { !it.optBoolean("ok", true) }?.let {
                out += if (it.isNull("error")) "Restore drill overdue" else "Restore drill failed"
            }
            if (s.failed("disk")) out += "Disk almost full"
            val certs = s.optJSONArray("certs")
            for (i in 0 until (certs?.length() ?: 0)) {
                val c = certs?.optJSONObject(i) ?: continue
                if (!c.isNull("ok") && !c.optBoolean("ok")) out += "Certificate: ${c.optString("target")}"
            }
        }
        return out
    }

    /** [key]'s check ran and failed ("ok": false); null / missing is not a failure. */
    private fun JSONObject.failed(key: String): Boolean =
        optJSONObject(key)?.let { !it.isNull("ok") && !it.optBoolean("ok") } == true
}
