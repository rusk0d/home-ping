package com.david.homeenv

import android.util.Base64
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject

/** Sends the FCM token to the backend (POST /api/register_token), which subscribes it to topic "env". */
object PushRegistration {

    /** Blocking network call: run it off the main thread. */
    fun register(token: String) {
        try {
            val client = OkHttpClient()
            val mediaType = "application/json; charset=utf-8".toMediaType()
            val json = JSONObject().apply { put("token", token) }
            val body = json.toString().toRequestBody(mediaType)

            val authHeader = "Basic " + Base64.encodeToString(
                "${BuildConfig.API_USER}:${BuildConfig.API_PASS}".toByteArray(),
                Base64.NO_WRAP
            )

            val request = Request.Builder()
                .url("${BuildConfig.API_BASE_URL}/api/register_token")
                .post(body)
                .addHeader("Authorization", authHeader)
                .build()

            client.newCall(request).execute().use { response ->
                if (!response.isSuccessful) {
                    println("Failed to register token: ${response.code}")
                } else {
                    println("Token registered successfully")
                }
            }
        } catch (e: Exception) {
            e.printStackTrace()
        }
    }
}
