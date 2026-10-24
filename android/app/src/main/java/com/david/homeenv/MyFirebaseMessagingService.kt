package com.david.homeenv

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.util.Log
import android.content.Intent
import androidx.core.app.NotificationCompat
import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage

class MyFirebaseMessagingService : FirebaseMessagingService() {

    companion object {
        private const val TAG = "FcmService"
        /** Sensor alerts (humidity, temperature, radar). Also the FCM default channel (manifest). */
        const val CHANNEL_SENSORS = "homeenv_channel"
        /** Arrival alerts, separate so they can stay on when sensor alerts are muted. */
        const val CHANNEL_OCCUPANCY = "occupancy"
        /** Air-out hints (#245): no sound or vibration, they're not urgent. */
        const val CHANNEL_AIR = "air"

        /** Creates the channels; called at app start so FCM can use them before the first message. */
        fun createChannels(context: Context) {
            val manager = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            manager.createNotificationChannel(
                NotificationChannel(CHANNEL_SENSORS, "Sensor alerts", NotificationManager.IMPORTANCE_HIGH)
            )
            manager.createNotificationChannel(
                NotificationChannel(CHANNEL_OCCUPANCY, "Occupancy alerts", NotificationManager.IMPORTANCE_HIGH)
            )
            manager.createNotificationChannel(
                NotificationChannel(CHANNEL_AIR, "Air quality", NotificationManager.IMPORTANCE_LOW)
            )
        }
    }

    override fun onMessageReceived(message: RemoteMessage) {
        super.onMessageReceived(message)

        // The backend sends data-only messages: title/body travel in data (#12)
        val data = message.data
        val title = data["title"] ?: "Home Ping"
        val body = data["body"] ?: ""

        sendNotification(title, body, data["event"], data["device"])
    }

    private fun sendNotification(title: String, body: String, event: String?, device: String?) {
        val intent = Intent(this, MainActivity::class.java)
        intent.flags = Intent.FLAG_ACTIVITY_CLEAR_TOP

        val pendingIntent = PendingIntent.getActivity(
            this, 0, intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        createChannels(this)
        val channelId = when (event) {
            "OCCUPIED" -> CHANNEL_OCCUPANCY
            "AIR" -> CHANNEL_AIR
            else -> CHANNEL_SENSORS
        }

        val notification = NotificationCompat.Builder(this, channelId)
            .setSmallIcon(R.drawable.ic_notification)   // white silhouette; a colour icon shows as a blob
            .setColor(getColor(R.color.good))
            .setContentTitle(title)
            .setContentText(body)
            .setStyle(NotificationCompat.BigTextStyle().bigText(body))
            .setAutoCancel(true)
            .setContentIntent(pendingIntent)
            .build()

        // One slot per alert type and device: a new humidity alert replaces the last one,
        // but doesn't wipe an occupancy alert. Unknown events each get their own slot.
        val id = if (event != null) "$event/$device".hashCode() else System.currentTimeMillis().toInt()
        val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        manager.notify(id, notification)
    }

    /**
     * The installation is registered with FCM (replaces onNewToken, #153). Nothing to send
     * to the backend: pushes go to topic "env", which the app subscribes to itself.
     */
    override fun onRegistered(installationId: String) {
        super.onRegistered(installationId)
        Log.i(TAG, "FCM registration changed")
    }
}
