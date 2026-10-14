package com.david.homeenv.ui

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.drawable.GradientDrawable
import android.util.AttributeSet
import android.widget.LinearLayout
import androidx.core.content.ContextCompat
import com.david.homeenv.R

/**
 * Card from the web dashboard: rounded corners, 1dp border and a 3dp coloured
 * strip along the top edge (`.card::before`). Children are clipped to the shape.
 */
class AccentCard @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
) : LinearLayout(context, attrs) {

    private val density = resources.displayMetrics.density
    private val stripHeight = 3 * density
    private val stripPaint = Paint(Paint.ANTI_ALIAS_FLAG)

    /** Strip colour; [Color.TRANSPARENT] hides it. */
    var accentColor: Int = Color.TRANSPARENT
        set(value) {
            field = value
            stripPaint.color = value
            invalidate()
        }

    /** Dims the card when its data is old (web: `.card.stale`). */
    var stale: Boolean = false
        set(value) {
            field = value
            alpha = if (value) 0.55f else 1f
        }

    init {
        // Orientation comes from XML (the HomeCard style sets vertical); don't override it here
        background = GradientDrawable().apply {
            cornerRadius = 18 * density
            setColor(ContextCompat.getColor(context, R.color.card))
            setStroke((1 * density).toInt().coerceAtLeast(1), ContextCompat.getColor(context, R.color.card_edge))
        }
        clipToOutline = true
        setWillNotDraw(false)
        context.obtainStyledAttributes(attrs, R.styleable.AccentCard).apply {
            accentColor = getColor(R.styleable.AccentCard_accentColor, Color.TRANSPARENT)
            recycle()
        }
    }

    override fun dispatchDraw(canvas: Canvas) {
        super.dispatchDraw(canvas)
        if (Color.alpha(accentColor) != 0) {
            canvas.drawRect(0f, 0f, width.toFloat(), stripHeight, stripPaint)
        }
    }
}
