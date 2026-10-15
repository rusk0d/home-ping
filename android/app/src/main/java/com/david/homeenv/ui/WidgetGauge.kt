package com.david.homeenv.ui

import android.content.Context
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.Path
import android.graphics.RectF
import androidx.annotation.ColorRes
import com.david.homeenv.R

/**
 * Gauge bars for the home-screen widget, drawn into a Bitmap because widgets
 * (RemoteViews) can't host custom Views. Same look as [Co2GaugeView]: a pill-shaped
 * bar in level colours with a needle at the current value, without the scale.
 * Band stops match the thresholds in [Levels].
 */
object WidgetGauge {

    /** A gauge: value range and the colour bands as (start fraction, colour). */
    class Scale(val min: Float, val max: Float, val bands: List<Pair<Float, Int>>)

    /** 14–30 °C; stops at 18 / 20 / 24 / 27 °C ([Levels.temperature]). */
    val TEMPERATURE = Scale(
        14f, 30f,
        listOf(0f to R.color.accent_hum, 0.25f to R.color.ok, 0.375f to R.color.good, 0.625f to R.color.ok, 0.8125f to R.color.bad)
    )

    /** 0–100 %RH; stops at 30 / 40 / 60 / 70 % ([Levels.humidity]). */
    val HUMIDITY = Scale(
        0f, 100f,
        listOf(0f to R.color.poor, 0.3f to R.color.ok, 0.4f to R.color.good, 0.6f to R.color.ok, 0.7f to R.color.poor)
    )

    /** Occupancy confidence 0–100 %; stops at 20 / 60 % ([Levels.occupancy]). */
    val OCCUPANCY = Scale(
        0f, 100f,
        listOf(0f to R.color.muted, 0.2f to R.color.ok, 0.6f to R.color.good)
    )

    /** Draws [scale] [widthPx] wide (16dp high), with the needle at [value] (null = no needle). */
    fun render(context: Context, scale: Scale, value: Double?, widthPx: Int): Bitmap {
        val d = context.resources.displayMetrics.density
        val barHeight = 8 * d
        val needleWidth = 4 * d
        val needleHeight = 16 * d
        val w = widthPx.coerceAtLeast((40 * d).toInt()).toFloat()

        val bitmap = Bitmap.createBitmap(w.toInt(), needleHeight.toInt(), Bitmap.Config.ARGB_8888)
        val canvas = Canvas(bitmap)
        val paint = Paint(Paint.ANTI_ALIAS_FLAG)
        val rect = RectF()

        // Bar, clipped to a pill shape; dimmed when there is no value
        val barTop = (needleHeight - barHeight) / 2
        rect.set(0f, barTop, w, barTop + barHeight)
        val clip = Path().apply { addRoundRect(rect, barHeight / 2, barHeight / 2, Path.Direction.CW) }
        canvas.save()
        canvas.clipPath(clip)
        scale.bands.forEachIndexed { i, (start, @ColorRes colorRes) ->
            val end = scale.bands.getOrNull(i + 1)?.first ?: 1f
            paint.color = Levels.withAlpha(Levels.color(context, colorRes), if (value == null) 0.3f else 0.9f)
            canvas.drawRect(start * w, barTop, end * w, barTop + barHeight, paint)
        }
        canvas.restore()

        // Needle with a thin card-coloured outline so it stands out on the bar
        value?.let {
            val f = ((it.toFloat() - scale.min) / (scale.max - scale.min)).coerceIn(0f, 1f)
            val x = f * (w - needleWidth) + needleWidth / 2
            val edge = 2 * d
            paint.color = Levels.color(context, R.color.card)
            rect.set(x - needleWidth / 2 - edge, 0f, x + needleWidth / 2 + edge, needleHeight)
            canvas.drawRoundRect(rect, 3 * d, 3 * d, paint)
            paint.color = Levels.color(context, R.color.text)
            rect.set(x - needleWidth / 2, edge, x + needleWidth / 2, needleHeight - edge)
            canvas.drawRoundRect(rect, 2 * d, 2 * d, paint)
        }
        return bitmap
    }
}
