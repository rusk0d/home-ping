package com.david.homeenv

import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity
import androidx.viewpager2.widget.ViewPager2
import com.david.homeenv.databinding.ActivityNotificationSettingsBinding
import com.google.android.material.tabs.TabLayout
import com.google.android.material.tabs.TabLayoutMediator

/**
 * Notification settings and history with tabs (#82, #289).
 */
class NotificationSettingsActivity : AppCompatActivity() {

    private lateinit var b: ActivityNotificationSettingsBinding

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        b = ActivityNotificationSettingsBinding.inflate(layoutInflater)
        setContentView(b.root)

        b.back.setOnClickListener { finish() }

        // Set up ViewPager2 with adapter
        val adapter = NotificationPagerAdapter(this)
        b.viewPager.adapter = adapter

        // Connect TabLayout to ViewPager2
        TabLayoutMediator(b.tabLayout, b.viewPager) { tab, position ->
            tab.text = when (position) {
                0 -> "Settings"
                1 -> "History"
                else -> "Unknown"
            }
        }.attach()
    }
}
