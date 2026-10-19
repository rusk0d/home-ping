package com.david.homeenv

import android.content.Context
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import androidx.work.workDataOf
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.util.concurrent.TimeUnit

/**
 * Sends a location arm/disarm (#239) and keeps retrying until the server has it. Leaving
 * home is often exactly when the phone drops WiFi, so a single post from the geofence
 * receiver could be lost and leave the alert unarmed. The work waits for a network and
 * backs off on failures. One unique job: a newer crossing replaces a pending older one,
 * so the server always ends up with the latest state.
 */
class ArmWorker(ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {

    override suspend fun doWork(): Result {
        val armed = inputData.getBoolean(KEY_ARMED, false)
        val status = withContext(Dispatchers.IO) { HomeGeofence.postArm(armed) }
        return when {
            status != null && status in 200..299 -> Result.success()
            status != null && status in 400..499 -> Result.failure()   // retrying won't help
            else -> Result.retry()   // no connection, timeout or a server error
        }
    }

    companion object {
        private const val KEY_ARMED = "armed"
        private const val WORK_NAME = "location-arm"

        fun enqueue(ctx: Context, armed: Boolean) {
            val request = OneTimeWorkRequestBuilder<ArmWorker>()
                .setInputData(workDataOf(KEY_ARMED to armed))
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS)
                .build()
            WorkManager.getInstance(ctx).enqueueUniqueWork(WORK_NAME, ExistingWorkPolicy.REPLACE, request)
        }

        /** A manual flip wins (#240): drop a location arm that is still waiting to be sent. */
        fun cancel(ctx: Context) {
            WorkManager.getInstance(ctx).cancelUniqueWork(WORK_NAME)
        }
    }
}
