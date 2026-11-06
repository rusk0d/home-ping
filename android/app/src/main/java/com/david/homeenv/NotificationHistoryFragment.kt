package com.david.homeenv

import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.LinearLayout
import android.widget.TextView
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import com.david.homeenv.databinding.FragmentNotificationHistoryBinding
import com.david.homeenv.ui.Levels
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Notification history: last 20 notifications that were sent (#289).
 * Filters events for ones that were actually delivered (status = "sent").
 */
class NotificationHistoryFragment : Fragment() {

    private lateinit var b: FragmentNotificationHistoryBinding

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View {
        b = FragmentNotificationHistoryBinding.inflate(inflater, container, false)
        return b.root
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        load()
    }

    private fun load() {
        lifecycleScope.launch {
            val result = withContext(Dispatchers.IO) {
                Api.fetchObject("/api/notification_history")
            }

            if (result.json == null) {
                b.status.text = "Can't reach the server: ${result.error}"
                return@launch
            }
            val data = result.json!!

            b.container.removeAllViews()
            b.container.addView(b.status)

            val events = data.optJSONArray("events") ?: JSONArray()

            // Filter for notifications that were actually sent (not muted or failed)
            val sentNotifications = mutableListOf<org.json.JSONObject>()
            for (i in 0 until events.length()) {
                val event = events.optJSONObject(i) ?: continue
                val value = event.optString("value", "")
                // Value format: "sent, ...", "muted (...), ...", or "failed, ..."
                if (value.startsWith("sent")) {
                    sentNotifications.add(event)
                }
            }

            if (sentNotifications.isEmpty()) {
                val empty = TextView(requireContext()).apply {
                    text = "No notifications sent yet"
                    setTextColor(Levels.color(requireContext(), R.color.muted))
                    textSize = 14f
                    layoutParams = LinearLayout.LayoutParams(
                        ViewGroup.LayoutParams.MATCH_PARENT,
                        ViewGroup.LayoutParams.WRAP_CONTENT
                    ).apply { topMargin = 24 }
                }
                b.container.addView(empty)
                return@launch
            }

            b.status.text = "Notifications sent to this phone"
            for (event in sentNotifications) {
                val row = eventRow(event)
                b.container.addView(row)
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
            ).apply { topMargin = (12 * d).toInt() }
            orientation = LinearLayout.VERTICAL
            setPadding((12 * d).toInt(), (8 * d).toInt(), (12 * d).toInt(), (8 * d).toInt())
            setBackgroundColor(Levels.color(ctx, R.color.faint))
        }

        val tsStr = event.optString("ts")
        val type = event.optString("type", "?")
        val device = event.optString("device", "")
        val value = event.optString("value", "")
        // Extract the detail part after "sent, "
        val detail = if (value.startsWith("sent, ")) value.substring(6) else value

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

        if (detail.isNotEmpty()) {
            val detailView = TextView(ctx).apply {
                text = detail
                setTextColor(Levels.color(ctx, R.color.muted))
                textSize = 12f
                setPadding(0, (2 * d).toInt(), 0, 0)
            }
            row.addView(detailView)
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
