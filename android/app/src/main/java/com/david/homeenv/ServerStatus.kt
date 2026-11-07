package com.david.homeenv

import android.view.ViewGroup
import android.widget.LinearLayout
import com.david.homeenv.ui.AccentCard
import com.david.homeenv.ui.Levels
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Locale

/**
 * The server's checks (#260) on the Devices screen (moved from Home, #325): one line per check
 * of /api/server_status, red where it fails, and a red strip when any does.
 */
object ServerStatus {
    fun fill(card: AccentCard, json: JSONObject) {
        val ctx = card.context
        val d = ctx.resources.displayMetrics.density
        val lines = mutableListOf<Triple<String, String, Boolean?>>()

        json.optJSONObject("backend")?.let { be ->
            val mqtt = be.optBoolean("mqtt_connected")
            val up = be.optTs("started")?.let { "up ${HomeFormat.uptime(((System.currentTimeMillis() - it.time) / 1000).toInt())}" }
            lines += Triple("Backend", listOfNotNull(up, if (mqtt) "MQTT connected" else "MQTT disconnected").joinToString(" · "), mqtt)
        }
        json.optJSONObject("backup")?.let { bk ->
            val last = bk.optTs("last")
            val meta = if (last == null) "none yet" else listOfNotNull(
                HomeFormat.whenText(last),
                bk.optNum("size_bytes")?.let { "%.1f MB".format(Locale.US, it / 1_000_000) },
                "${bk.optInt("count")} kept",
            ).joinToString(" · ")
            lines += Triple("Backup", meta, bk.optBoolean("ok"))
        }
        json.optJSONObject("offsite")?.let { off ->
            val meta = off.optTs("last")?.let { "uploaded ${HomeFormat.whenText(it)}" } ?: "never uploaded"
            lines += Triple("S3 copy", meta, off.optBoolean("ok"))
        }
        // The monthly restore drill (#339/#340): when, which backup, or why it failed
        json.optJSONObject("drill")?.let { dr ->
            val last = dr.optTs("last")
            val error = if (dr.isNull("error")) null else dr.optString("error")
            val backup = if (dr.isNull("backup")) null else dr.optString("backup").substringAfterLast('/')
            val meta = when {
                last == null -> "never ran"
                error != null -> "${HomeFormat.whenText(last)} · $error"
                else -> listOfNotNull(HomeFormat.whenText(last), backup?.let { "$it restored" }).joinToString(" · ")
            }
            lines += Triple("Restore drill", meta, dr.optBoolean("ok"))
        }
        json.optJSONObject("disk")?.let { disk ->
            val mb = disk.optInt("free_mb")
            val free = if (mb >= 1024) "%.1f GB free".format(Locale.US, mb / 1024.0) else "$mb MB free"
            lines += Triple("Disk", free, disk.optBoolean("ok"))
        }
        json.optJSONArray("certs")?.let { certs ->
            for (i in 0 until certs.length()) {
                val c = certs.optJSONObject(i) ?: continue
                val expires = c.optTs("expires")
                val error = if (c.isNull("error")) null else c.optString("error")
                val detail = when {
                    error != null -> error
                    expires != null -> {
                        val days = (expires.time - System.currentTimeMillis()) / 86_400_000
                        "until ${SimpleDateFormat("yyyy-MM-dd", Locale.US).format(expires)} ($days d)"
                    }
                    else -> "not checked yet"
                }
                val ok = if (c.isNull("ok")) null else c.optBoolean("ok")
                lines += Triple("Certificate", "${c.optString("target")} · $detail", ok)
            }
        }

        card.removeAllViews()
        lines.forEachIndexed { i, (name, meta, ok) ->
            val line = HomeFormat.statusLine(ctx, name, meta, ok)
            if (i > 0) {
                line.layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
                    .apply { topMargin = (10 * d).toInt() }
            }
            card.addView(line)
        }
        // Red strip when anything fails, as the watchdog would push
        val anyBad = lines.any { it.third == false }
        card.accentColor = Levels.color(ctx, if (anyBad) R.color.bad else R.color.good)
    }
}
