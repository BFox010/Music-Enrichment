import { api } from "../api.js";

let _calChart = null;
let _hwChart = null;

export async function init(calId, hwId) {
  const calEl = document.getElementById(calId);
  const hwEl  = document.getElementById(hwId);
  if (!calEl || !hwEl) return;

  _calChart = echarts.init(calEl, "dark");
  _hwChart  = echarts.init(hwEl,  "dark");
  window.addEventListener("resize", () => {
    _calChart?.resize();
    _hwChart?.resize();
  });

  const data = await api.timeOfDay();
  updateCalendar(data);
  updateHeatmap(data);
}

function updateCalendar(data) {
  if (!_calChart || !data?.calendar?.length) return;
  const dates = data.calendar.map(d => d[0]);
  const first = dates[0];
  const last  = dates[dates.length - 1];
  const max   = Math.max(...data.calendar.map(d => d[1]));

  _calChart.setOption({
    backgroundColor: "transparent",
    title: { text: "Listening Calendar", textStyle: { color: "#ddd", fontSize: 13 }, top: 4 },
    tooltip: { formatter: p => `${p.data[0]}: ${p.data[1]} plays` },
    visualMap: {
      min: 0, max,
      type: "continuous",
      orient: "horizontal",
      left: "center",
      bottom: 4,
      inRange: { color: ["#1a1a2e", "#7b5ea7", "#e040fb"] },
      textStyle: { color: "#aaa" },
    },
    calendar: [{
      top: 40,
      left: 30,
      right: 10,
      range: [first, last],
      cellSize: ["auto", 14],
      itemStyle: { borderWidth: 0.5, borderColor: "#2a2a2a" },
      yearLabel: { color: "#aaa" },
      dayLabel: { color: "#888", nameMap: ["Sun","Mon","Tue","Wed","Thu","Fri","Sat"] },
      monthLabel: { color: "#aaa" },
    }],
    series: [{
      type: "heatmap",
      coordinateSystem: "calendar",
      data: data.calendar,
    }],
  });
}

function updateHeatmap(data) {
  if (!_hwChart || !data?.hour_weekday?.length) return;
  const days  = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  const hours = Array.from({ length: 24 }, (_, i) => `${i}:00`);
  const max   = Math.max(...data.hour_weekday.map(d => d[2]));

  _hwChart.setOption({
    backgroundColor: "transparent",
    title: { text: "Hour × Weekday", textStyle: { color: "#ddd", fontSize: 13 }, top: 4 },
    tooltip: {
      formatter: p => {
        const [dow, h, n] = p.data;
        return `${days[dow]} ${hours[h]}: ${n} plays`;
      },
    },
    grid: { top: 36, bottom: 36, left: 48, right: 12 },
    xAxis: {
      type: "category",
      data: days,
      axisLabel: { color: "#aaa" },
      axisLine: { lineStyle: { color: "#444" } },
      splitArea: { show: true },
    },
    yAxis: {
      type: "category",
      data: hours,
      axisLabel: { color: "#aaa", fontSize: 9 },
      axisLine: { lineStyle: { color: "#444" } },
      splitArea: { show: true },
    },
    visualMap: {
      min: 0, max,
      calculable: true,
      orient: "horizontal",
      left: "center",
      bottom: 0,
      inRange: { color: ["#1a1a2e", "#7b5ea7", "#e040fb"] },
      textStyle: { color: "#aaa" },
    },
    series: [{
      type: "heatmap",
      // API gives [hour, dow, count]; chart wants [dow, hour, count]
      data: data.hour_weekday.map(([h, dow, n]) => [dow, h, n]),
      label: { show: false },
      emphasis: { itemStyle: { shadowBlur: 8, shadowColor: "rgba(0,0,0,0.5)" } },
    }],
  });
}
