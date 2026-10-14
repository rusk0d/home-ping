package com.david.homeenv

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.graphics.Color
import android.os.Build
import android.os.Bundle
import android.util.Base64
import android.view.Gravity
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Switch
import android.widget.TableRow
import android.widget.TextView
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.fragment.app.Fragment
import androidx.fragment.app.FragmentActivity
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout
import androidx.viewpager2.adapter.FragmentStateAdapter
import androidx.viewpager2.widget.ViewPager2
import com.google.android.material.tabs.TabLayout
import com.google.android.material.tabs.TabLayoutMediator
import com.google.firebase.FirebaseApp
import com.google.firebase.messaging.FirebaseMessaging
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Locale
import kotlin.concurrent.thread
import com.david.homeenv.BuildConfig
class MainActivity : AppCompatActivity() {

    private lateinit var viewPager: ViewPager2
    private lateinit var tabLayout: TabLayout

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        // Check and request exact alarm permission for Android 12+
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            val alarmManager = getSystemService(Context.ALARM_SERVICE) as android.app.AlarmManager
            if (!alarmManager.canScheduleExactAlarms()) {
                val intent = android.content.Intent(
                    android.provider.Settings.ACTION_REQUEST_SCHEDULE_EXACT_ALARM
                )
                try {
                    startActivity(intent)
                } catch (e: Exception) {
                    e.printStackTrace()
                }
            }
        }

        // Push notifications (off in the preview build, which has no Firebase config)
        if (BuildConfig.PUSH_ENABLED) setupPush()

        // Setup ViewPager and TabLayout
        viewPager = findViewById(R.id.viewPager)
        tabLayout = findViewById(R.id.tabLayout)

        val adapter = ViewPagerAdapter(this)
        viewPager.adapter = adapter

        TabLayoutMediator(tabLayout, viewPager) { tab, position ->
            tab.text = when (position) {
                0 -> "Home"
                1 -> "Plants"
                else -> "Tab $position"
            }
        }.attach()
    }

    private fun setupPush() {
        if (FirebaseApp.getApps(this).isEmpty()) {
            FirebaseApp.initializeApp(this)
        }

        // Subscribe to topic for notifications
        FirebaseMessaging.getInstance().subscribeToTopic("env")
            .addOnCompleteListener { task ->
                println("Subscribed to topic 'env': ${task.isSuccessful}")
            }

        // Request notification permission on Android 13+
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            when {
                ContextCompat.checkSelfPermission(
                    this,
                    Manifest.permission.POST_NOTIFICATIONS
                ) == PackageManager.PERMISSION_GRANTED -> getAndRegisterToken()
                else -> requestPermissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
            }
        } else {
            getAndRegisterToken()
        }
    }

    private val requestPermissionLauncher =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
            if (granted) getAndRegisterToken()
        }

    private fun getAndRegisterToken() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
            != PackageManager.PERMISSION_GRANTED
        ) return

        FirebaseMessaging.getInstance().token
            .addOnSuccessListener { token ->
                println("FCM token: $token")
                Thread { registerTokenServerSide(token) }.start()
            }
            .addOnFailureListener {
                it.printStackTrace()
            }
    }

    private fun registerTokenServerSide(token: String) {
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

    // ViewPager Adapter
    private inner class ViewPagerAdapter(fa: FragmentActivity) : FragmentStateAdapter(fa) {
        override fun getItemCount(): Int = 2

        override fun createFragment(position: Int): Fragment {
            return when (position) {
                0 -> HomeFragment()
                1 -> PlantsFragment()
                else -> HomeFragment()
            }
        }
    }
}

// Home Environment Fragment
class HomeFragment : Fragment() {

    private val credentials = "${BuildConfig.API_USER}:${BuildConfig.API_PASS}"
    private val latestAllUrl = "${BuildConfig.API_BASE_URL}/api/latest_all"
    private val devicesUrl = "${BuildConfig.API_BASE_URL}/api/devices"
    private val stats24hUrl = "${BuildConfig.API_BASE_URL}/api/stats24h_combined"
    private val historyCombinedUrl = "${BuildConfig.API_BASE_URL}/api/history_combined"
    private val occupancyArmUrl = "${BuildConfig.API_BASE_URL}/api/occupancy_arm"
    private val occupancyStatusUrl = "${BuildConfig.API_BASE_URL}/api/occupancy_status"

    private val airUrl = "${BuildConfig.API_BASE_URL}/api/air"

    private val hourlyUrl = "${BuildConfig.API_BASE_URL}/api/hourly"

    private val restartsUrl = "${BuildConfig.API_BASE_URL}/api/restarts"

    private lateinit var swipeRefresh: SwipeRefreshLayout
    private lateinit var occupancySwitch: Switch
    private lateinit var occupancyStatus: TextView
    private lateinit var tempValue: TextView
    private lateinit var humidityValue: TextView
    private lateinit var occupiedValue: TextView
    private lateinit var confidenceValue: TextView
    private lateinit var oledStatus: TextView
    private lateinit var einkStatus: TextView
    private lateinit var oledUptime: TextView
    private lateinit var einkUptime: TextView
    private lateinit var statsAvgTemp: TextView
    private lateinit var statsAvgHumidity: TextView
    private lateinit var statsAvgConfidence: TextView
    private lateinit var statsOccupiedHours: TextView
    private lateinit var readingsTable: android.widget.TableLayout
    private lateinit var co2Value: TextView
    private lateinit var co2Band: TextView
    private lateinit var co2Status: TextView
    private lateinit var co2SensorStatus: TextView
    private lateinit var co2Uptime: TextView

    private lateinit var pressureValue: TextView

    private lateinit var pressureTrend: TextView

    private lateinit var pressureOutlook: TextView

    private lateinit var occupancyBars: android.widget.LinearLayout

    private lateinit var occLabelStart: TextView

    private lateinit var occLabelEnd: TextView

    private lateinit var restartsValue: TextView

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View? {
        return inflater.inflate(R.layout.fragment_home, container, false)
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)

        // Initialize views
        swipeRefresh = view.findViewById(R.id.swipeRefresh)
        occupancySwitch = view.findViewById(R.id.occupancySwitch)
        occupancyStatus = view.findViewById(R.id.occupancyStatus)
        tempValue = view.findViewById(R.id.temp_value)
        humidityValue = view.findViewById(R.id.humidity_value)
        occupiedValue = view.findViewById(R.id.occupied_value)
        confidenceValue = view.findViewById(R.id.confidence_value)
        oledStatus = view.findViewById(R.id.oled_status)
        einkStatus = view.findViewById(R.id.eink_status)
        oledUptime = view.findViewById(R.id.oled_uptime)
        einkUptime = view.findViewById(R.id.eink_uptime)
        statsAvgTemp = view.findViewById(R.id.stats_avg_temp)
        statsAvgHumidity = view.findViewById(R.id.stats_avg_humidity)
        statsAvgConfidence = view.findViewById(R.id.stats_avg_confidence)
        statsOccupiedHours = view.findViewById(R.id.stats_occupied_hours)
        readingsTable = view.findViewById(R.id.readings_table)
        co2Value = view.findViewById(R.id.co2_value)
        co2Band = view.findViewById(R.id.co2_band)
        co2Status = view.findViewById(R.id.co2_status)
        co2SensorStatus = view.findViewById(R.id.co2_sensor_status)
        co2Uptime = view.findViewById(R.id.co2_uptime)
        pressureValue = view.findViewById(R.id.pressure_value)
        pressureTrend = view.findViewById(R.id.pressure_trend)
        pressureOutlook = view.findViewById(R.id.pressure_outlook)
        occupancyBars = view.findViewById(R.id.occupancy_bars)
        occLabelStart = view.findViewById(R.id.occ_label_start)
        occLabelEnd = view.findViewById(R.id.occ_label_end)
        restartsValue = view.findViewById(R.id.restarts_value)
        // Setup swipe refresh
        swipeRefresh.setOnRefreshListener {
            loadDashboardData()
        }

        // Setup occupancy toggle
        occupancySwitch.setOnCheckedChangeListener { _, isChecked ->
            setOccupancyArmed(isChecked)
        }

        // Load initial data
        loadDashboardData()
        loadOccupancyArmedState()
    }

    private fun setOccupancyArmed(armed: Boolean) {
        thread {
            try {
                val client = OkHttpClient()
                val mediaType = "application/json; charset=utf-8".toMediaType()
                val json = JSONObject().apply { put("armed", armed) }
                val body = json.toString().toRequestBody(mediaType)

                val authHeader = "Basic " + Base64.encodeToString(
                    credentials.toByteArray(),
                    Base64.NO_WRAP
                )

                val request = Request.Builder()
                    .url(occupancyArmUrl)
                    .post(body)
                    .addHeader("Authorization", authHeader)
                    .build()

                client.newCall(request).execute().use { response ->
                    if (!response.isSuccessful) {
                        println("Failed to set occupancy arm: ${response.code}")
                        activity?.runOnUiThread { occupancySwitch.isChecked = !armed }
                    } else {
                        activity?.runOnUiThread { updateOccupancyStatus(armed) }
                    }
                }
            } catch (e: Exception) {
                e.printStackTrace()
                activity?.runOnUiThread { occupancySwitch.isChecked = !armed }
            }
        }
    }

    private fun updateOccupancyStatus(armed: Boolean) {
        occupancyStatus.text = if (armed) "Notifications: ARMED" else "Notifications: DISARMED"
    }

    private fun loadOccupancyArmedState() {
        thread {
            try {
                val client = OkHttpClient()
                val authHeader = "Basic " + Base64.encodeToString(
                    credentials.toByteArray(),
                    Base64.NO_WRAP
                )

                val request = Request.Builder()
                    .url(occupancyStatusUrl)
                    .addHeader("Authorization", authHeader)
                    .build()

                val response = client.newCall(request).execute()
                if (response.isSuccessful) {
                    val body = response.body?.string() ?: return@thread
                    val json = JSONObject(body)
                    val armed = json.getBoolean("armed")

                    activity?.runOnUiThread {
                        occupancySwitch.setOnCheckedChangeListener(null)
                        occupancySwitch.isChecked = armed
                        occupancySwitch.setOnCheckedChangeListener { _, isChecked ->
                            setOccupancyArmed(isChecked)
                        }
                        updateOccupancyStatus(armed)
                    }
                }
            } catch (e: Exception) {
                e.printStackTrace()
            }
        }
    }

    private fun formatUptime(seconds: Int): String {
        val days = seconds / 86400
        val hours = (seconds % 86400) / 3600
        val minutes = (seconds % 3600) / 60

        return buildString {
            if (days > 0) append("${days}d ")
            if (hours > 0) append("${hours}h ")
            append("${minutes}m")
        }.trim()
    }

    private fun formatHours(hours: Double): String {
        val h = hours.toInt()
        val m = ((hours - h) * 60).toInt()
        return "${h}h ${m}m"
    }

    private fun loadDashboardData() {
        thread {
            try {
                val client = OkHttpClient()
                val authHeader = "Basic " + Base64.encodeToString(
                    credentials.toByteArray(),
                    Base64.NO_WRAP
                )

                // Get latest data from all devices
                val latestRequest = Request.Builder()
                    .url(latestAllUrl)
                    .addHeader("Authorization", authHeader)
                    .build()

                val latestResponse = client.newCall(latestRequest).execute()
                if (latestResponse.isSuccessful) {
                    val latestBody = latestResponse.body?.string() ?: return@thread
                    val latestJson = JSONObject(latestBody)

                    activity?.runOnUiThread {
                        // OLED device data
                        if (latestJson.has("home_ping_esp32")) {
                            val oledData = latestJson.getJSONObject("home_ping_esp32")
                            tempValue.text = String.format("%.1f °C", oledData.getDouble("temp"))
                            humidityValue.text = "${oledData.getInt("humidity")} %"
                            oledUptime.text = "Ping: ${formatUptime(oledData.getInt("uptime"))}"
                        }

                        // E-ink device data
                        if (latestJson.has("hallway_presence_esp32")) {
                            val einkData = latestJson.getJSONObject("hallway_presence_esp32")
                            einkUptime.text = "Hallway: ${formatUptime(einkData.getInt("uptime"))}"
                        } else {
                            einkUptime.text = "Hallway: --"
                        }
                        // CO2 node
                        if (latestJson.has("co2_sensor_esp32")) {
                            val co2Data = latestJson.getJSONObject("co2_sensor_esp32")

                            if (!co2Data.isNull("co2")) {
                                val ppm = co2Data.getInt("co2")
                                co2Value.text = "$ppm"

                                val (label, colour) = when {
                                    ppm < 800  -> Pair("Fresh", "#4ade80")
                                    ppm < 1200 -> Pair("OK", "#fbbf24")
                                    ppm < 2000 -> Pair("Stuffy", "#fb923c")
                                    else       -> Pair("Air it out", "#f87171")
                                }
                                co2Band.text = label
                                co2Band.setTextColor(Color.parseColor(colour))
                                co2Value.setTextColor(Color.parseColor(colour))
                            } else {
                                co2Value.text = "--"
                                co2Band.text = "No reading"
                                co2Band.setTextColor(Color.parseColor("#94a3b8"))
                                co2Value.setTextColor(Color.parseColor("#e5e7eb"))
                            }

                            co2Uptime.text =
                                "CO2: ${formatUptime(co2Data.getInt("uptime"))}"

                            // Sensor health, separate from the ESP32 being online
                            val sensorOk = if (co2Data.isNull("co2_ok")) null
                            else co2Data.getBoolean("co2_ok")
                            when (sensorOk) {
                                true -> {
                                    co2SensorStatus.text = "CO2 sensor: OK"
                                    co2SensorStatus.setTextColor(Color.parseColor("#4ade80"))
                                }
                                false -> {
                                    co2SensorStatus.text = "CO2 sensor: Fault"
                                    co2SensorStatus.setTextColor(Color.parseColor("#f87171"))
                                }
                                else -> {
                                    co2SensorStatus.text = "CO2 sensor: --"
                                    co2SensorStatus.setTextColor(Color.parseColor("#94a3b8"))
                                }
                            }
                        } else {
                            co2Uptime.text = "CO2 Uptime: --"
                            co2SensorStatus.text = "CO2 sensor: --"
                        }
                        // Combined occupancy data
                        if (latestJson.has("combined")) {
                            val combined = latestJson.getJSONObject("combined")
                            val occupiedInt = combined.getInt("occupied")
                            val occupied = occupiedInt == 1
                            val confidence = combined.getInt("confidence")

                            occupiedValue.text = if (occupied) "Yes" else "No"
                            occupiedValue.setTextColor(
                                if (occupied) Color.parseColor("#4ade80")
                                else Color.parseColor("#f87171")
                            )

                            confidenceValue.text = "$confidence %"
                        }
                    }
                }

                // Get device statuses
                val devicesRequest = Request.Builder()
                    .url(devicesUrl)
                    .addHeader("Authorization", authHeader)
                    .build()

                val devicesResponse = client.newCall(devicesRequest).execute()
                if (devicesResponse.isSuccessful) {
                    val devicesBody = devicesResponse.body?.string() ?: return@thread
                    val devicesArray = JSONArray(devicesBody)

                    activity?.runOnUiThread {
                        for (i in 0 until devicesArray.length()) {
                            val device = devicesArray.getJSONObject(i)
                            val deviceName = device.getString("device")
                            val online = device.getBoolean("online")

                            when (deviceName) {
                                "home_ping_esp32" -> {
                                    oledStatus.text = "Ping: ${if (online) "Online" else "Offline"}"
                                    oledStatus.setTextColor(
                                        if (online) Color.parseColor("#4ade80")
                                        else Color.parseColor("#f87171")
                                    )
                                }
                                "hallway_presence_esp32" -> {
                                    einkStatus.text = "Hallway: ${if (online) "Online" else "Offline"}"
                                    einkStatus.setTextColor(
                                        if (online) Color.parseColor("#4ade80")
                                        else Color.parseColor("#f87171")
                                    )

                                }
                                "co2_sensor_esp32" -> {
                                    co2Status.text = "CO2: ${if (online) "Online" else "Offline"}"
                                    co2Status.setTextColor(
                                        if (online) Color.parseColor("#4ade80")
                                        else Color.parseColor("#f87171")
                                    )
                                }
                            }
                        }
                    }
                }

                // Get 24h stats
                val statsRequest = Request.Builder()
                    .url(stats24hUrl)
                    .addHeader("Authorization", authHeader)
                    .build()

                val statsResponse = client.newCall(statsRequest).execute()
                if (statsResponse.isSuccessful) {
                    val statsBody = statsResponse.body?.string() ?: return@thread
                    val stats = JSONObject(statsBody)

                    activity?.runOnUiThread {
                        statsAvgTemp.text = String.format("%.1f °C", stats.getDouble("avg_temp"))
                        statsAvgHumidity.text = "${stats.getInt("avg_humidity")} %"
                        statsAvgConfidence.text = "${stats.getInt("avg_confidence")} %"
                        statsOccupiedHours.text = formatHours(stats.getDouble("occupied_hours"))
                    }
                }
                val restartsRequest = Request.Builder()
                    .url(restartsUrl)
                    .addHeader("Authorization", authHeader)
                    .build()

                val restartsResponse = client.newCall(restartsRequest).execute()
                if (restartsResponse.isSuccessful) {
                    val body = restartsResponse.body?.string() ?: return@thread
                    val json = JSONObject(body)

                    activity?.runOnUiThread {
                        val total24h = json.keys().asSequence().sumOf {
                            json.getJSONObject(it).getInt("last_24h")
                        }
                        restartsValue.text = when (total24h) {
                            0 -> "None in 24h"
                            1 -> "1 in 24h"
                            else -> "$total24h in 24h"
                        }
                        restartsValue.setTextColor(
                            when {
                                total24h == 0 -> Color.parseColor("#4ade80")
                                total24h <= 2 -> Color.parseColor("#fbbf24")
                                else          -> Color.parseColor("#f87171")
                            }
                        )
                    }
                }
                // Get last 30 readings
                val historyRequest = Request.Builder()
                    .url(historyCombinedUrl)
                    .addHeader("Authorization", authHeader)
                    .build()

                val historyResponse = client.newCall(historyRequest).execute()
                if (historyResponse.isSuccessful) {
                    val historyBody = historyResponse.body?.string() ?: return@thread
                    val history = JSONArray(historyBody)

                    activity?.runOnUiThread {
                        // Clear existing rows except header
                        val childCount = readingsTable.childCount
                        if (childCount > 1) {
                            readingsTable.removeViews(1, childCount - 1)
                        }

                        // Add data rows
                        for (i in 0 until history.length()) {
                            val reading = history.getJSONArray(i)

                            val row = TableRow(requireContext())
                            row.setPadding(0, 8, 0, 8)

                            // Time
                            val timeStr = reading.getString(0)
                            val inputFormat = SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US)
                            val outputFormat = SimpleDateFormat("HH:mm", Locale.US)
                            val formattedTime = try {
                                val date = inputFormat.parse(timeStr)
                                outputFormat.format(date!!)
                            } catch (e: Exception) {
                                timeStr
                            }

                            row.addView(createTableCell(formattedTime))

                            // Device (shortened)
                            val device = reading.getString(1)
                            val deviceShort = when {
                                device.contains("oled") || device.contains("ping") -> "OLED"
                                device.contains("hallway") -> "Hallway"
                                else -> device.take(5)
                            }
                            row.addView(createTableCell(deviceShort))

                            // Temp
                            val temp = if (!reading.isNull(2)) {
                                String.format("%.1f°C", reading.getDouble(2))
                            } else "--"
                            row.addView(createTableCell(temp))

                            // Humidity
                            val hum = if (!reading.isNull(3)) {
                                "${reading.getInt(3)}%"
                            } else "--"
                            row.addView(createTableCell(hum))

                            // Occupied
                            val occ = if (reading.getInt(4) == 1) "Yes" else "No"
                            row.addView(createTableCell(occ))

                            // Confidence
                            val conf = "${reading.getInt(5)}%"
                            row.addView(createTableCell(conf))

                            readingsTable.addView(row)
                        }

                        swipeRefresh.isRefreshing = false
                    }
                }
                // Air: pressure + trend
                val airRequest = Request.Builder()
                    .url(airUrl)
                    .addHeader("Authorization", authHeader)
                    .build()

                val airResponse = client.newCall(airRequest).execute()
                if (airResponse.isSuccessful) {
                    val airBody = airResponse.body?.string() ?: return@thread
                    val air = JSONObject(airBody)

                    activity?.runOnUiThread {
                        if (!air.isNull("pressure")) {
                            pressureValue.text =
                                String.format("%.0f hPa", air.getDouble("pressure"))
                        } else {
                            pressureValue.text = "-- hPa"
                        }

                        if (!air.isNull("pressure_3h_change")) {
                            val d = air.getDouble("pressure_3h_change")
                            pressureTrend.text = String.format("%+.1f", d)

                            val (word, colour) = when {
                                d <= -3.0 -> Pair("Storm likely", "#f87171")
                                d <= -1.0 -> Pair("Falling", "#fbbf24")
                                d <  1.0  -> Pair("Steady", "#4ade80")
                                d <  3.0  -> Pair("Rising", "#4ade80")
                                else      -> Pair("Clearing fast", "#60a5fa")
                            }
                            pressureOutlook.text = word
                            pressureOutlook.setTextColor(Color.parseColor(colour))
                            pressureTrend.setTextColor(Color.parseColor(colour))
                        } else {
                            pressureTrend.text = "--"
                            pressureOutlook.text = "not enough data"
                            pressureOutlook.setTextColor(Color.parseColor("#94a3b8"))
                        }
                    }
                }

                // Hourly occupancy strip
                val hourlyRequest = Request.Builder()
                    .url(hourlyUrl)
                    .addHeader("Authorization", authHeader)
                    .build()

                val hourlyResponse = client.newCall(hourlyRequest).execute()
                if (hourlyResponse.isSuccessful) {
                    val hourlyBody = hourlyResponse.body?.string() ?: return@thread
                    val hourly = JSONArray(hourlyBody)

                    activity?.runOnUiThread {
                        occupancyBars.removeAllViews()

                        // API returns newest first — reverse so time runs left to right
                        val items = (0 until hourly.length())
                            .map { hourly.getJSONObject(it) }
                            .reversed()

                        for (item in items) {
                            val pct = if (item.isNull("avg_occupancy")) 0.0
                            else item.getDouble("avg_occupancy")

                            val bar = View(requireContext())
                            val lp = android.widget.LinearLayout.LayoutParams(
                                0,
                                // minimum 2dp so empty hours still show as a baseline
                                maxOf((pct / 100.0 * 64).toInt(), 2).dpToPx()
                            )
                            lp.weight = 1f
                            lp.marginEnd = 2.dpToPx()
                            bar.layoutParams = lp

                            bar.setBackgroundColor(
                                when {
                                    pct >= 60 -> Color.parseColor("#4ade80")
                                    pct >= 20 -> Color.parseColor("#fbbf24")
                                    pct > 0   -> Color.parseColor("#64748b")
                                    else      -> Color.parseColor("#334155")
                                }
                            )
                            occupancyBars.addView(bar)
                        }

                        // Label the left edge with the oldest hour we have
                        if (items.isNotEmpty()) {
                            val firstHour = items.first().getString("hour")
                            occLabelStart.text = firstHour.takeLast(5)   // "HH:00"
                        }
                    }
                }

            } catch (e: Exception) {
                e.printStackTrace()
                activity?.runOnUiThread {
                    tempValue.text = "Error"
                    humidityValue.text = "Error"
                    swipeRefresh.isRefreshing = false
                }
            }
        }
    }

    private fun createTableCell(text: String): TextView {
        return TextView(requireContext()).apply {
            this.text = text
            textSize = 12f
            setTextColor(Color.parseColor("#e5e7eb"))
            setPadding(8, 4, 16, 4)
            gravity = Gravity.START
        }
    }
    private fun Int.dpToPx(): Int =
        (this * resources.displayMetrics.density).toInt()
}

// Plants Monitor Fragment
class PlantsFragment : Fragment() {

    private val soilLatestUrl = "${BuildConfig.API_BASE_URL}/api/soil/latest"
    private val credentials = "${BuildConfig.API_USER}:${BuildConfig.API_PASS}"

    private lateinit var swipeRefresh: SwipeRefreshLayout
    private lateinit var soilMoistureValue: TextView
    private lateinit var soilStatus: TextView
    private lateinit var soilLastUpdate: TextView

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View? {
        return inflater.inflate(R.layout.fragment_plants, container, false)
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)

        // Initialize views
        swipeRefresh = view.findViewById(R.id.swipeRefreshPlants)
        soilMoistureValue = view.findViewById(R.id.soil_moisture_value)
        soilStatus = view.findViewById(R.id.soil_status)
        soilLastUpdate = view.findViewById(R.id.soil_last_update)

        // Setup swipe refresh
        swipeRefresh.setOnRefreshListener {
            loadSoilData()
        }

        // Load initial data
        loadSoilData()
    }

    private fun loadSoilData() {
        thread {
            try {
                val client = OkHttpClient()
                val authHeader = "Basic " + Base64.encodeToString(
                    credentials.toByteArray(),
                    Base64.NO_WRAP
                )

                val soilRequest = Request.Builder()
                    .url(soilLatestUrl)
                    .addHeader("Authorization", authHeader)
                    .build()

                val soilResponse = client.newCall(soilRequest).execute()
                if (soilResponse.isSuccessful) {
                    val soilBody = soilResponse.body?.string() ?: return@thread
                    val soil = JSONObject(soilBody)

                    activity?.runOnUiThread {
                        val moisture = if (soil.has("moisture")) {
                            soil.getInt("moisture")
                        } else {
                            0
                        }

                        soilMoistureValue.text = "$moisture %"

                        val (status, color) = when {
                            moisture == 0 -> Pair("No data", Color.parseColor("#94a3b8"))
                            moisture < 30 -> Pair("Needs Water", Color.parseColor("#f87171"))
                            moisture < 50 -> Pair("Low", Color.parseColor("#fbbf24"))
                            moisture < 70 -> Pair("Good", Color.parseColor("#4ade80"))
                            else -> Pair("Wet", Color.parseColor("#60a5fa"))
                        }

                        soilStatus.text = status
                        soilStatus.setTextColor(color)

                        if (soil.has("timestamp")) {
                            try {
                                val timestamp = soil.getString("timestamp")
                                val inputFormat = SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US)
                                val outputFormat = SimpleDateFormat("MMM dd, HH:mm", Locale.US)
                                val date = inputFormat.parse(timestamp)
                                soilLastUpdate.text = "Last update: ${outputFormat.format(date!!)}"
                            } catch (e: Exception) {
                                soilLastUpdate.text = "Last update: ${soil.optString("timestamp", "--")}"
                            }
                        } else if (soil.has("ts")) {
                            try {
                                val timestamp = soil.getString("ts")
                                val inputFormat = SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US)
                                val outputFormat = SimpleDateFormat("MMM dd, HH:mm", Locale.US)
                                val date = inputFormat.parse(timestamp)
                                soilLastUpdate.text = "Last update: ${outputFormat.format(date!!)}"
                            } catch (e: Exception) {
                                soilLastUpdate.text = "Last update: --"
                            }
                        } else {
                            soilLastUpdate.text = "Last update: --"
                        }

                        swipeRefresh.isRefreshing = false
                    }
                } else {
                    activity?.runOnUiThread {
                        soilMoistureValue.text = "-- %"
                        soilStatus.text = "No data"
                        soilLastUpdate.text = "Last update: --"
                        swipeRefresh.isRefreshing = false
                    }
                }

            } catch (e: Exception) {
                e.printStackTrace()
                activity?.runOnUiThread {
                    soilMoistureValue.text = "Error"
                    soilStatus.text = "Error"
                    swipeRefresh.isRefreshing = false
                }
            }
        }
    }
}
