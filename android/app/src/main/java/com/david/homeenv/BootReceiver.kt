package com.david.homeenv

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/**
 * Geofences are dropped on reboot (#239), and can be after an app update: add the home one
 * again. The Home screen also re-adds it whenever it opens.
 */
class BootReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action == Intent.ACTION_BOOT_COMPLETED || intent.action == Intent.ACTION_MY_PACKAGE_REPLACED) {
            HomeGeofence.register(context)
        }
    }
}
