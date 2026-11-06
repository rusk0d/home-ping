package com.david.homeenv

import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.LinearLayout
import android.widget.TextView
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import com.david.homeenv.ui.Levels
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Notification history: last 20 notification events (#289).
 */
class NotificationHistoryFragment : Fragment() {

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View {
        return LinearLayout(requireContext()).apply {
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT
            )
            orientation = LinearLayout.VERTICAL
            setPadding(16, 16, 16, 32)
        }
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        val container = view as LinearLayout
        val status = TextView(requireContext()).apply {
            text = "Loading…"
            setTextColor(Levels.color(requireContext(), R.color.muted))
            textSize = 13f
        }
        container.addView(status)

        lifecycleScope.launch {
            val result = withContext(Dispatchers.IO) {
                Api.fetchObject("/api/notification_history")
            }

            if (result.json == null) {
                status.text = "Can't reach the server: ${result.error}"
                return@launch
            }
            val data = result.json!!

            container.removeAllViews()
            val events = data.optJSONArray("events") ?: JSONArray()

            if (events.length() == 0) {
                val empty = TextView(requireContext()).apply {
                    text = "No notifications yet"
                    setTextColor(Levels.color(requireContext(), R.color.muted))
                    textSize = 14f
                }
                container.addView(empty)
                return@launch
            }

            for (i in 0 until events.length()) {
                val event = events.optJSONObject(i) ?: continue
                val row = eventRow(event)
                container.addView(row)
            }
        }
    }

    private fun eventRow(event: org.json.JSONObject): View {
        val ctx = requireContext()
        val d = resources.displayMetrics.density
        val row = LinearLayout(ctx).apply {
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            ).apply { bottomMargin = (12 * d).toInt() }
            orientation = LinearLayout.VERTICAL
            setPadding((12 * d).toInt(), (8 * d).toInt(), (12 * d).toInt(), (8 * d).toInt())
            setBackgroundColor(Levels.color(ctx, R.color.faint))
        }

        val tsStr = event.optString("ts")
        val type = event.optString("type", "?")
        val device = event.optString("device", "")
        val value = event.optString("value", "")

        val header = TextView(ctx).apply {
            text = "$type${if (device.isNotEmpty()) " · $device" else ""}"
            setTextColor(Levels.color(ctx, R.color.text))
            textSize = 14f
            paint.isFakeBoldText = true
        }
        row.addView(header)

        val timestamp = parseTs(tsStr)?.let {
            SimpleDateFormat("MMM d, HH:mm:ss", Locale.US).format(it)
        } ?: tsStr
        val time = TextView(ctx).apply {
            text = timestamp
            setTextColor(Levels.color(ctx, R.color.muted))
            textSize = 12f
            setPadding(0, (4 * d).toInt(), 0, 0)
        }
        row.addView(time)

        if (value.isNotEmpty()) {
            val detail = TextView(ctx).apply {
                text = value
                setTextColor(Levels.color(ctx, R.color.muted))
                textSize = 12f
                setPadding(0, (2 * d).toInt(), 0, 0)
            }
            row.addView(detail)
        }

        return row
    }

    private fun parseTs(s: String?): Date? {
        if (s.isNullOrEmpty()) return null
        return runCatching {
            Date.from(java.time.Instant.parse(s))
        }.getOrNull()
    }
}
