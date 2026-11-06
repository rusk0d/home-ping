package com.david.homeenv

import androidx.fragment.app.Fragment
import androidx.fragment.app.FragmentActivity
import androidx.viewpager2.adapter.FragmentStateAdapter

/**
 * Pager adapter for notification settings and history tabs (#289).
 */
class NotificationPagerAdapter(activity: FragmentActivity) : FragmentStateAdapter(activity) {
    companion object {
        const val TAB_SETTINGS = 0
        const val TAB_HISTORY = 1
        const val TAB_COUNT = 2
    }

    override fun getItemCount(): Int = TAB_COUNT

    override fun createFragment(position: Int): Fragment {
        return when (position) {
            TAB_SETTINGS -> NotificationSettingsFragment()
            TAB_HISTORY -> NotificationHistoryFragment()
            else -> NotificationSettingsFragment()
        }
    }
}
