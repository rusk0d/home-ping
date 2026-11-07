package com.david.homeenv

import android.content.Context
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
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Locale

/**
 * The last 20 pushes the server sent (`/api/notification_history`, #289, #296), newest first.
 * "Show muted" adds the alerts that were muted (#321); a tap opens the screen an entry is about.
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
        // Remembered on this phone only: it's a view setting, not a notification setting
        b.showMuted.isChecked = prefs().getBoolean(PREF_SHOW_MUTED, false)
        b.showMuted.setOnCheckedChangeListener { _, on ->
            prefs().edit().putBoolean(PREF_SHOW_MUTED, on).apply()
            load()
        }
    }

    // Each time the tab is shown (ViewPager2 resumes only the visible page), not just once
    override fun onResume() {
        super.onResume()
        load()
    }

    private fun prefs() = requireContext().getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    private fun load() {
        val muted = b.showMuted.isChecked
        viewLifecycleOwner.lifecycleScope.launch {
            val result = withContext(Dispatchers.IO) {
                Api.fetchObject("/api/notification_history" + if (muted) "?muted=1" else "")
            }
            // The switch changed while this was loading: that load's result counts
            if (muted != b.showMuted.isChecked) return@launch

            b.list.removeAllViews()
            val data = result.json
            if (data == null) {
                b.status.text = "Can't reach the server: ${result.error}"
                return@launch
            }

            val events = data.optJSONArray("events") ?: JSONArray()
            if (events.length() == 0) {
                b.status.text = if (muted) "No notifications sent or muted yet" else "No notifications sent yet"
                return@launch
            }

            b.status.text = if (muted) "The last ${events.length()} alerts, sent to every phone or muted · tap to open"
                else "The last ${events.length()} pushes, to every phone · tap to open"
            for (i in 0 until events.length()) {
                events.optJSONObject(i)?.let { b.list.addView(eventRow(it)) }
            }
        }
    }

    private fun eventRow(event: JSONObject): View {
        val ctx = requireContext()
        val d = resources.displayMetrics.density
        val type = event.optString("type", "?")
        val device = event.optString("device", "")
        val detail = event.optString("detail", "")
        val muted = event.optString("status") == "muted"
        // optString() turns a JSON null into "null"
        val why = event.takeUnless { it.isNull("muted_reason") }?.optString("muted_reason")

        val row = LinearLayout(ctx).apply {
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            ).apply { topMargin = (12 * d).toInt() }
            orientation = LinearLayout.VERTICAL
            setPadding((12 * d).toInt(), (8 * d).toInt(), (12 * d).toInt(), (8 * d).toInt())
            setBackgroundColor(Levels.color(ctx, R.color.faint))
            // A muted one is dimmed: it never reached the phones
            alpha = if (muted) 0.6f else 1f
            isClickable = true
            setOnClickListener { startActivity(Screens.forEventType(ctx, type)) }
        }

        row.addView(TextView(ctx).apply {
            text = "$type${if (device.isNotEmpty()) " · $device" else ""}"
            setTextColor(Levels.color(ctx, R.color.text))
            textSize = 14f
            paint.isFakeBoldText = true
        })

        val timestamp = event.optTs("ts")?.let {
            SimpleDateFormat("MMM d, HH:mm:ss", Locale.US).format(it)
        } ?: event.optString("ts")
        row.addView(TextView(ctx).apply {
            text = if (muted) "$timestamp · muted (${why ?: "?"})" else timestamp
            setTextColor(Levels.color(ctx, if (muted) R.color.ok else R.color.muted))
            textSize = 12f
            setPadding(0, (4 * d).toInt(), 0, 0)
        })

        if (detail.isNotEmpty()) {
            row.addView(TextView(ctx).apply {
                text = detail
                setTextColor(Levels.color(ctx, R.color.muted))
                textSize = 12f
                setPadding(0, (2 * d).toInt(), 0, 0)
            })
        }

        return row
    }

    private companion object {
        const val PREFS = "notification_history"
        const val PREF_SHOW_MUTED = "show_muted"
    }
}
