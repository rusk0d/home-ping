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
import java.util.Locale

/** The last 20 pushes the server sent (`/api/notification_history`, #289, #296), newest first. */
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

    // Each time the tab is shown (ViewPager2 resumes only the visible page), not just once
    override fun onResume() {
        super.onResume()
        load()
    }

    private fun load() {
        viewLifecycleOwner.lifecycleScope.launch {
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
            if (events.length() == 0) {
                b.status.text = "No notifications sent yet"
                return@launch
            }

            b.status.text = "The last ${events.length()} pushes, to every phone"
            for (i in 0 until events.length()) {
                events.optJSONObject(i)?.let { b.container.addView(eventRow(it)) }
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
        val detail = event.optString("detail", "")

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
}
