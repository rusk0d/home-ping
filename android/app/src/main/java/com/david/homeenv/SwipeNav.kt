package com.david.homeenv

import android.app.Activity
import android.content.Context
import android.os.Build
import android.view.GestureDetector
import android.view.MotionEvent
import kotlin.math.abs

/**
 * Swipe navigation between Home and Devices (#325): Devices sits to the left of Home, so a
 * swipe right on Home opens it and a swipe left on Devices goes back. Feed it every touch
 * from Activity.dispatchTouchEvent(); vertical scrolling and pull-to-refresh are unaffected.
 */
class SwipeNav(context: Context, private val onRight: () -> Unit = {}, private val onLeft: () -> Unit = {}) {
    private val d = context.resources.displayMetrics.density

    private val detector = GestureDetector(context, object : GestureDetector.SimpleOnGestureListener() {
        override fun onFling(e1: MotionEvent?, e2: MotionEvent, vx: Float, vy: Float): Boolean {
            val start = e1 ?: return false
            val dx = e2.x - start.x
            val dy = e2.y - start.y
            // Clearly sideways, far and fast enough: not a scroll that drifted
            if (abs(dx) < 96 * d || abs(dx) < 2 * abs(dy) || abs(vx) < 500 * d) return false
            if (dx > 0) onRight() else onLeft()
            return true
        }
    })

    fun onTouch(ev: MotionEvent) {
        detector.onTouchEvent(ev)
    }

    companion object {
        /** In DevicesActivity.onCreate(): it slides in from the left and out to the left. */
        fun slideFromLeft(activity: Activity) {
            if (Build.VERSION.SDK_INT >= 34) {
                activity.overrideActivityTransition(Activity.OVERRIDE_TRANSITION_OPEN, R.anim.slide_in_left, R.anim.slide_out_right)
                activity.overrideActivityTransition(Activity.OVERRIDE_TRANSITION_CLOSE, R.anim.slide_in_right, R.anim.slide_out_left)
            } else {
                @Suppress("DEPRECATION")
                activity.overridePendingTransition(R.anim.slide_in_left, R.anim.slide_out_right)
            }
        }

        /** In DevicesActivity.finish(), after super.finish() (API < 34; overrideActivityTransition covers 34+). */
        fun slideBackLeft(activity: Activity) {
            if (Build.VERSION.SDK_INT < 34) {
                @Suppress("DEPRECATION")
                activity.overridePendingTransition(R.anim.slide_in_right, R.anim.slide_out_left)
            }
        }
    }
}
