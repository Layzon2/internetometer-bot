/* Интернетометр — Telegram Web App.
 * Замер скорости идёт прямо с устройства (браузера в Telegram) через Cloudflare.
 * Собранные результаты можно отправить боту через Telegram.WebApp.sendData().
 */
"use strict";

const CF = "https://speed.cloudflare.com";
const MB = 1024 * 1024;
const DOWN_MAX_BYTES = 300 * MB;   // верхняя граница объёма загрузки
const DOWN_MIN_SECONDS = 8;        // минимальная длительность замера загрузки
const UP_PROBE_BYTES = 4 * MB;     // пробник отдачи (перекрывает буфер)
const UP_MAX_BYTES = 32 * MB;      // максимум отдачи в браузере
const UP_SLOW_SECONDS = 3.0;

const $ = (id) => document.getElementById(id);
const state = { result: null, running: false };

const tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;

/* -------------------------------- helpers -------------------------------- */

function humanBytes(n) {
  if (!n || n <= 0) return "0 Б";
  const units = ["Б", "КБ", "МБ", "ГБ", "ТБ"];
  let i = 0, v = n;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return (i === 0 ? v : v.toFixed(1)).toString().replace(".", ",") + " " + units[i];
}

function fmtMbps(v) {
  if (v === null || v === undefined || isNaN(v)) return "—";
  return (v >= 100 ? v.toFixed(0) : v.toFixed(1)) + " Мбит/с";
}

function verdict(down, ping) {
  const parts = [];
  if (down != null) {
    if (down >= 100) parts.push("🚀 гигабитный уровень");
    else if (down >= 50) parts.push("✅ отлично для 4K");
    else if (down >= 25) parts.push("✅ хватает для 4K");
    else if (down >= 10) parts.push("👍 Full HD без проблем");
    else if (down >= 3) parts.push("📺 HD и видеозвонки");
    else parts.push("⚠️ только сёрфинг и музыка");
  }
  if (ping != null) {
    if (ping <= 20) parts.push("🎮 идеально для игр");
    else if (ping <= 50) parts.push("🎮 комфортно для игр");
    else if (ping <= 100) parts.push("🎮 игры возможны");
    else parts.push("⚠️ высокий пинг для игр");
  }
  return parts.join(" · ");
}

function toast(msg) {
  const el = $("toast");
  el.textContent = msg;
  el.classList.remove("hidden");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.add("hidden"), 2600);
}

function setRunning(on) {
  state.running = on;
  $("btn-start").disabled = on;
  $("btn-start").textContent = on ? "⏳ Идёт замер..." : "🚀 Начать тест";
}

function setBar(pct) {
  $("bar").style.width = Math.max(0, Math.min(100, pct)) + "%";
}

/* -------------------------------- network info -------------------------------- */

async function fetchMeta() {
  const r = await fetch(CF + "/meta", { headers: { "Accept": "application/json" } });
  if (!r.ok) throw new Error("meta " + r.status);
  return await r.json();
}

async function refreshNet() {
  $("net-ip").textContent = "…";
  try {
    const m = await fetchMeta();
    $("net-ip").textContent = m.clientIp || "—";
    $("net-org").textContent = m.asOrganization || "—";
    $("net-asn").textContent = m.asn ? "AS" + m.asn : "—";
    $("net-loc").textContent = [m.city, m.region, m.country].filter(Boolean).join(", ") || "—";
    let colo = m.colo;
    if (colo && typeof colo === "object") colo = colo.iata || colo.city || null;
    $("net-colo").textContent = colo || "—";
    $("net-proto").textContent = m.httpProtocol || "—";
    toast("Данные сети обновлены ✅");
  } catch (e) {
    $("net-ip").textContent = "—";
    toast("Не удалось получить данные сети");
  }
}

/* -------------------------------- ping -------------------------------- */

async function measurePing(samples) {
  // Несколько маленьких запросов; за «пинг» берём минимум (мин-время).
  const times = [];
  for (let i = 0; i < samples; i++) {
    const t0 = performance.now();
    try {
      await fetch(CF + "/__down?bytes=10000", { cache: "no-store" });
      times.push(performance.now() - t0);
    } catch (e) { /* пропускаем неудачный сэмпл */ }
  }
  if (!times.length) return null;
  return Math.round(Math.min.apply(null, times));
}

/* -------------------------------- download -------------------------------- */

async function measureDownload(onProgress) {
  // Прогрев — разгоняет TLS и канал.
  await fetch(CF + "/__down?bytes=1000000", { cache: "no-store" });

  const resp = await fetch(CF + "/__down?bytes=" + DOWN_MAX_BYTES, { cache: "no-store" });
  if (!resp.ok || !resp.body) throw new Error("Hет потока ответа");

  const reader = resp.body.getReader();
  let total = 0;
  const start = performance.now();
  let lastReport = start;

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value ? value.length : 0;
    const now = performance.now();
    const elapsed = (now - start) / 1000;
    if (now - lastReport >= 500 && elapsed > 0) {
      lastReport = now;
      onProgress(total, total * 8 / elapsed / 1e6, elapsed);
    }
    if (now - start >= DOWN_MIN_SECONDS * 1000) break;
  }
  try { await reader.cancel(); } catch (e) { /* ок */ }

  const elapsed = (performance.now() - start) / 1000;
  if (total === 0 || elapsed <= 0) throw new Error("пустой результат");
  return { mbps: total * 8 / elapsed / 1e6, bytes: total, seconds: elapsed };
}

/* -------------------------------- upload -------------------------------- */

async function postUpload(bytes) {
  const payload = new Uint8Array(bytes);
  const t0 = performance.now();
  const r = await fetch(CF + "/__up", {
    method: "POST",
    body: payload,
    headers: { "Content-Type": "application/octet-stream" },
  });
  await r.text();
  const elapsed = (performance.now() - t0) / 1000;
  if (elapsed <= 0) throw new Error("пустой результат");
  return { mbps: bytes * 8 / elapsed / 1e6, bytes: bytes, seconds: elapsed };
}

async function measureUpload() {
  // Пробник 4 МБ, затем добор на быстрых каналах (как в боте).
  let res = await postUpload(UP_PROBE_BYTES);
  if (!res.mbps) throw new Error("отдача не измерена");
  if (res.seconds >= UP_SLOW_SECONDS) return res; // медленный канал — выборки хватает

  const bps = res.mbps * 1e6 / 8;
  const mainBytes = Math.min(UP_MAX_BYTES, Math.max(1 * MB, Math.round(bps * 7)));
  const res2 = await postUpload(mainBytes);
  if (!res2.mbps) return res;
  return {
    mbps: Math.max(res.mbps, res2.mbps),
    bytes: res.bytes + res2.bytes,
    seconds: res.seconds + res2.seconds,
  };
}

/* -------------------------------- speed test -------------------------------- */

async function runSpeed() {
  if (state.running) return;
  state.result = null;
  setRunning(true);
  $("verdict").classList.add("hidden");
  $("details").classList.add("hidden");
  $("btn-send").classList.add("hidden");
  $("btn-retry").classList.add("hidden");
  $("g-down").textContent = "—";
  $("g-up").textContent = "—";
  $("g-ping").textContent = "…";
  setBar(3);

  try {
    $("status").innerHTML = "🏓 Замеряю задержку (пинг)...";
    const ping = await measurePing(5);
    $("g-ping").textContent = ping != null ? ping + " мс" : "—";

    $("status").innerHTML = "⬇️ Замеряю загрузку (≈8 секунд)...";
    setBar(12);
    const down = await measureDownload((total, mbps, sec) => {
      $("g-down").textContent = fmtMbps(mbps);
      $("status").innerHTML =
        "⬇️ Загрузка: <b>" + fmtMbps(mbps) + "</b> · " +
        humanBytes(total) + " · " + sec.toFixed(1) + " с";
      setBar(12 + Math.min(66, (sec / DOWN_MIN_SECONDS) * 70));
    });
    $("g-down").textContent = fmtMbps(down.mbps);

    $("status").innerHTML = "⬆️ Замеряю отдачу (пробник + добор)...";
    setBar(78);
    const up = await measureUpload();
    $("g-up").textContent = fmtMbps(up.mbps);

    let meta = null;
    try { meta = await fetchMeta(); } catch (e) { /* необязательно */ }

    state.result = {
      ping_ms: ping,
      download_mbps: down.mbps,
      upload_mbps: up.mbps,
      downloaded_bytes: down.bytes,
      uploaded_bytes: up.bytes,
      download_seconds: down.seconds,
      upload_seconds: up.seconds,
      meta: meta,
    };

    setBar(100);
    $("status").innerHTML = "✅ Замер завершён!";
    const v = verdict(down.mbps, ping);
    const vEl = $("verdict");
    vEl.textContent = "🧠 Оценка: " + v;
    vEl.classList.remove("bad");
    if ((down.mbps != null && down.mbps < 3) || (ping != null && ping > 120)) vEl.classList.add("bad");
    vEl.classList.remove("hidden");

    const metaLines = [];
    if (meta) {
      const loc = [meta.city, meta.country].filter(Boolean).join(", ");
      if (meta.clientIp) metaLines.push("🌐 IP: " + meta.clientIp);
      if (meta.asOrganization) metaLines.push("🏢 " + meta.asOrganization);
      if (loc) metaLines.push("📍 " + loc);
    }
    $("details").innerHTML = [
      "📦 Выборка: ↓ " + humanBytes(down.bytes) + " за " + down.seconds.toFixed(1) + " с" +
        " · ↑ " + humanBytes(up.bytes) + " за " + up.seconds.toFixed(1) + " с",
      metaLines.join("<br>"),
    ].filter(Boolean).join("<br>");
    $("details").classList.remove("hidden");

    $("btn-send").classList.remove("hidden");
    $("btn-retry").classList.remove("hidden");
  } catch (e) {
    $("status").innerHTML = "❌ Замер не удался: " + (e && e.message ? e.message : e);
    setBar(0);
  } finally {
    setRunning(false);
  }
}

function sendToBot() {
  const r = state.result;
  if (!r) {
    toast("Сначала запустите тест 🚀");
    return;
  }
  const payload = {
    action: "speedtest",
    ping_ms: r.ping_ms,
    download_mbps: r.download_mbps,
    upload_mbps: r.upload_mbps,
    downloaded_bytes: r.downloaded_bytes,
    uploaded_bytes: r.uploaded_bytes,
  };
  if (r.meta && r.meta.clientIp) {
    payload.client_ip = r.meta.clientIp;
    payload.provider = r.meta.asOrganization || null;
    payload.city = r.meta.city || null;
  }
  if (tg) {
    tg.sendData(JSON.stringify(payload));
    toast("Отправлено в бота ✅");
  } else {
    toast("Вне Telegram отправка недоступна");
  }
}

/* -------------------------------- calculator -------------------------------- */

const UNITS = { kb: 1024, mb: 1024 * 1024, gb: 1024 ** 3, tb: 1024 ** 4 };

function calcUpdate() {
  const sizeV = parseFloat($("calc-size").value);
  const spdV = parseFloat($("calc-speed").value);
  const sizeB = sizeV >= 0 ? sizeV * UNITS[$("calc-size-unit").value] : NaN;
  let mbps = spdV >= 0 ? spdV : NaN;
  if ($("calc-speed-unit").value === "mbs") mbps = mbps * 8; // МБ/с → Мбит/с

  const out = $("calc-result");
  if (isNaN(sizeB) || isNaN(mbps) || mbps <= 0 || sizeB <= 0) {
    out.innerHTML = "—";
    return;
  }
  const sec = sizeB * 8 / mbps / 1e6;
  const t = formatDuration(sec);
  out.innerHTML =
    "⏱ Скачается за <b>" + t + "</b><br>" +
    "<span style='font-size:12.5px;color:#93a2b1'>" + humanBytes(sizeB) + " на " + fmtMbps(mbps) + "</span>";
}

function formatDuration(sec) {
  if (sec < 60) return Math.max(1, Math.round(sec)) + " с";
  if (sec < 3600) {
    const m = Math.floor(sec / 60), s = Math.round(sec % 60);
    return m + " мин " + s + " с";
  }
  const h = Math.floor(sec / 3600), m = Math.round((sec % 3600) / 60);
  return h + " ч " + m + " мин";
}

/* -------------------------------- tabs / init -------------------------------- */

function switchTab(name) {
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  document.querySelectorAll(".panel").forEach((p) => p.classList.toggle("active", p.id === "tab-" + name));
  if (name === "net") refreshNet();
}

function init() {
  document.querySelectorAll(".tab").forEach((t) =>
    t.addEventListener("click", () => switchTab(t.dataset.tab))
  );
  $("btn-start").addEventListener("click", runSpeed);
  $("btn-retry").addEventListener("click", runSpeed);
  $("btn-send").addEventListener("click", sendToBot);
  $("btn-net-refresh").addEventListener("click", refreshNet);

  ["calc-size", "calc-speed"].forEach((id) =>
    $(id).addEventListener("input", calcUpdate)
  );
  ["calc-size-unit", "calc-speed-unit"].forEach((id) =>
    $(id).addEventListener("change", calcUpdate)
  );
  calcUpdate();

  try {
    if (tg) {
      tg.ready();
      tg.expand();
    }
  } catch (e) { /* настройки виджета */ }
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}