package com.david.homeenv

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject

/**
 * What needs a look on the Devices screen (#325): the red dot on the header's devices button
 * and the list at the top of the screen. From /api/devices (`online`, `health`, #322),
 * /api/restarts and /api/server_status (#260; the restore drill since #340); a missing part adds nothing.
 *
 * An issue can be ignored until it changes (#380): e.g. restarts after flashing a node. Each has
 * a [Issue.key] (what it is about) and a [Issue.version] (its current state: the last restart's
 * time, last_seen, the drill's run): ignored = hidden while the version stays the same. Once the
 * issue has been gone for a day (the restarts aged out of 24 h), its ignore is dropped, so it
 * counts again next time.
 */
object DeviceIssues {
    /** WiFi below this (dBm) drops and reconnects often: the "poor" band on the Devices screen. */
    const val WEAK_RSSI = -80
    /** More restarts than this in 24 h is worth a look even without a crash reason. */
    const val MAX_RESTARTS_24H = 2

    /** One issue: [text] for the list; [key] and [version] for ignoring it (#380). */
    class Issue(val key: String, val text: String, val version: String = "")

    fun list(devices: JSONArray?, restarts: JSONObject?, server: JSONObject?): List<Issue> {
        val out = mutableListOf<Issue>()
        // Only the nodes in the backend's registry: a retired one stays listed for 30 days (#359)
        for (i in 0 until (devices?.length() ?: 0)) {
            val d = devices?.optJSONObject(i) ?: continue
            val id = d.optString("device")
            val name = d.optString("name").ifEmpty { id }
            if (!d.optBoolean("online") && d.optBoolean("known", true)) {
                out += Issue("offline:$id", "$name is offline", d.optString("last_seen"))
            }
            d.optJSONObject("health")?.optNum("rssi")?.toInt()?.takeIf { it < WEAK_RSSI }?.let {
                out += Issue("wifi:$id", "$name: weak WiFi ($it dBm)")
            }
        }
        restarts?.keys()?.forEach { id ->
            val r = restarts.optJSONObject(id) ?: return@forEach
            val name = Device.list(devices).firstOrNull { it.id == id }?.name ?: id
            val day = r.optInt("last_24h")
            val reason = r.takeUnless { it.isNull("last_reason") }?.optString("last_reason")
            // Another restart is a change: it shows again
            val last = r.optString("last_restart")
            when {
                day > MAX_RESTARTS_24H -> out += Issue("restarts:$id", "$name: $day restarts in 24 h", last)
                day > 0 && reason != null && HomeFormat.BAD_RESET_REASONS.any { reason.contains(it) } ->
                    out += Issue("restarts:$id", "$name: $reason reset", last)
            }
        }
        server?.let { s ->
            s.optJSONObject("backend")?.takeIf { !it.optBoolean("mqtt_connected", true) }?.let {
                out += Issue("mqtt", "Backend: MQTT disconnected")
            }
            if (s.failed("backup")) out += Issue("backup", "Backup overdue", s.lastOf("backup"))
            if (s.failed("offsite")) out += Issue("offsite", "S3 copy overdue", s.lastOf("offsite"))
            s.optJSONObject("drill")?.takeIf { !it.optBoolean("ok", true) }?.let {
                val text = if (it.isNull("error")) "Restore drill overdue" else "Restore drill failed"
                out += Issue("drill", text, s.lastOf("drill"))
            }
            if (s.failed("disk")) out += Issue("disk", "Disk almost full")
            val certs = s.optJSONArray("certs")
            for (i in 0 until (certs?.length() ?: 0)) {
                val c = certs?.optJSONObject(i) ?: continue
                if (!c.isNull("ok") && !c.optBoolean("ok")) {
                    val target = c.optString("target")
                    out += Issue("cert:$target", "Certificate: $target", c.optString("expires") + c.optString("error"))
                }
            }
        }
        return out
    }

    /** [key]'s check ran and failed ("ok": false); null / missing is not a failure. */
    private fun JSONObject.failed(key: String): Boolean =
        optJSONObject(key)?.let { !it.isNull("ok") && !it.optBoolean("ok") } == true

    /** [key]'s "last" (when it last ran / succeeded), the version of its issue. */
    private fun JSONObject.lastOf(key: String): String =
        optJSONObject(key)?.takeUnless { it.isNull("last") }?.optString("last").orEmpty()

    // ---------- ignored issues (#380), on this phone ----------
    // SharedPreferences: key -> "<ignored version>\n<when its issue was first seen gone, ms, or empty>"

    private const val PREFS = "device_issues_ignored"
    /** An ignore is dropped once its issue has been gone this long: not at once, so an issue
     *  that comes and goes (WiFi around -80 dBm) stays ignored. */
    private const val FORGET_AFTER_MS = 24 * 60 * 60 * 1000L
    private fun prefs(ctx: Context) = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    /**
     * The [issues] that aren't ignored. [complete]: every source loaded (devices, restarts and
     * server status); only then is an ignore's issue known to be gone, so a request that failed
     * doesn't start forgetting it.
     */
    fun visible(ctx: Context, issues: List<Issue>, complete: Boolean, now: Long = System.currentTimeMillis()): List<Issue> {
        val stored = prefs(ctx).all.mapValues { (it.value as? String).orEmpty().split("\n", limit = 2) }
        if (complete) {
            val current = issues.associateBy { it.key }
            val edit = prefs(ctx).edit()
            for ((key, v) in stored) {
                val version = v[0]
                val goneSince = v.getOrNull(1)?.toLongOrNull()
                when {
                    key in current && goneSince != null -> edit.putString(key, version + "\n")   // back
                    key !in current && goneSince == null -> edit.putString(key, "$version\n$now")
                    key !in current && now - goneSince!! > FORGET_AFTER_MS -> edit.remove(key)
                }
            }
            edit.apply()
        }
        return issues.filter { stored[it.key]?.get(0) != it.version }
    }

    /** Hide [issue] until its version changes (or it's gone for a day). */
    fun ignore(ctx: Context, issue: Issue) {
        prefs(ctx).edit().putString(issue.key, issue.version + "\n").apply()
    }

    fun unignoreAll(ctx: Context) {
        prefs(ctx).edit().clear().apply()
    }
}
