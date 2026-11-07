package com.david.homeenv

import android.content.Context
import android.text.SpannableStringBuilder
import android.text.Spanned
import android.text.style.ForegroundColorSpan
import android.text.style.RelativeSizeSpan
import android.view.Gravity
import android.view.View
import android.widget.LinearLayout
import android.widget.TextView
import com.david.homeenv.ui.Levels
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Date
import java.util.Locale

/** Text and small views the Home cards share (#306, R5). */
object HomeFormat {

    /** "22.4" + "°C" → big number with a small muted unit. */
    fun valueWithUnit(ctx: Context, value: String?, unit: String): CharSequence {
        if (value == null) return "--"
        val muted = Levels.color(ctx, R.color.muted)
        return SpannableStringBuilder(value).apply {
            val start = length
            append(" ").append(unit)
            setSpan(RelativeSizeSpan(0.45f), start, length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
            setSpan(ForegroundColorSpan(muted), start, length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
        }
    }

    /** A dot (good / bad, faint for null) and a bold name over a muted line; devices and Server card. */
    fun statusLine(ctx: Context, name: String, meta: String, ok: Boolean?): LinearLayout {
        val d = ctx.resources.displayMetrics.density
        val row = LinearLayout(ctx).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        val dot = View(ctx).apply {
            setBackgroundResource(R.drawable.dot)
            backgroundTintList = android.content.res.ColorStateList.valueOf(
                Levels.color(ctx, when (ok) { true -> R.color.good; false -> R.color.bad; null -> R.color.faint })
            )
            layoutParams = LinearLayout.LayoutParams((8 * d).toInt(), (8 * d).toInt()).apply { marginEnd = (12 * d).toInt() }
        }
        val texts = LinearLayout(ctx).apply { orientation = LinearLayout.VERTICAL }
        texts.addView(TextView(ctx).apply {
            text = name
            setTextColor(Levels.color(ctx, R.color.text))
            textSize = 15f
            paint.isFakeBoldText = true
        })
        texts.addView(TextView(ctx).apply {
            text = meta
            setTextColor(Levels.color(ctx, R.color.muted))
            textSize = 12f
        })
        row.addView(dot)
        row.addView(texts)
        return row
    }

    fun age(seconds: Long?): String = when {
        seconds == null -> "waiting…"
        seconds < 5 -> "just now"
        seconds < 60 -> "${seconds}s ago"
        seconds < 3600 -> "${seconds / 60}m ago"
        else -> "${seconds / 3600}h ago"
    }

    /** "today 03:30", "yesterday 03:30" or "Oct 18 03:30", local time. */
    fun whenText(t: Date): String {
        val then = Calendar.getInstance().apply { time = t }
        val now = Calendar.getInstance()
        val daysAgo = (now.get(Calendar.YEAR) - then.get(Calendar.YEAR)) * 365 +
            now.get(Calendar.DAY_OF_YEAR) - then.get(Calendar.DAY_OF_YEAR)
        val hhmm = SimpleDateFormat("HH:mm", Locale.getDefault()).format(t)
        return when (daysAgo) {
            0 -> "today $hhmm"
            1 -> "yesterday $hhmm"
            else -> SimpleDateFormat("MMM d", Locale.US).format(t) + " $hhmm"
        }
    }

    fun uptime(seconds: Int): String {
        val days = seconds / 86400
        val hours = (seconds % 86400) / 3600
        val minutes = (seconds % 3600) / 60
        return when {
            days > 0 -> "${days}d ${hours}h"
            hours > 0 -> "${hours}h ${minutes}m"
            else -> "${minutes}m"
        }
    }

    fun hours(hours: Double): String {
        val h = hours.toInt()
        val m = ((hours - h) * 60).toInt()
        return "${h}h ${m}m"
    }
}
