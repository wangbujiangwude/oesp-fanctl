#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OESP风扇助手 - Web 监控页（零依赖，仅标准库）
读取 oesp-fanctl 守护进程的状态文件/历史曲线/配置，提供：
  GET  /             监控面板（CPU/GPU/硬盘温度曲线，12H）
  GET  /api/state    实时状态 JSON
  GET  /api/history  12H 历史曲线 JSON
  POST /api/config   修改温度阈值与运行模式 {"TEMP_ON":45,"FAN_MODE":"on"}
                     FAN_MODE: auto=自动温控 / on=常开(一直运行) / off=常关
"""
import json
import os
import re
import time
import socket
import socketserver
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CONF = os.environ.get("OESP_FANCTL_CONF", "/etc/oesp-fanctl.conf")
STATE = os.environ.get("OESP_FANCTL_STATE", "/run/oesp-fanctl.state")
HISTORY = os.environ.get("OESP_FANCTL_HISTORY", "/var/log/oesp-fanctl-history.csv")
PORT = int(os.environ.get("WEB_PORT", "8765"))
# fnOS 网关模式：设置 OESP_FANCTL_SOCK 后监听 Unix Socket，经系统统一网关 /app/<base> 访问
SOCK = os.environ.get("OESP_FANCTL_SOCK", "")
BASE = os.environ.get("OESP_FANCTL_BASE", "").rstrip("/")

EDITABLE = {
    "TEMP_ON": (20, 90), "TEMP_OFF": (10, 85),
    "HDD_ON_TEMP": (30, 70),
    "HDD_RUN_SECONDS": (30, 1800),
}
# 字符串型配置：FAN_MODE = auto(自动温控) / on(常开，一直运行) / off(常关)
EDITABLE_STR = {
    "FAN_MODE": ("auto", "on", "off"),
}


def read_kv_file(path, skip_comments=True):
    """读取 key=value 文件为 dict（保留顺序）"""
    out = {}
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line or "=" not in line:
                    continue
                if skip_comments and line.startswith("#"):
                    continue
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().split("#")[0].strip() if skip_comments else v.strip()
    except OSError:
        pass
    return out


def read_state():
    st = read_kv_file(STATE, skip_comments=False)
    disks = []
    for k, v in st.items():
        if k.startswith("disk_") and k != "disk_max":
            try:
                disks.append({"name": k[5:], "temp": float(v)})
            except ValueError:
                disks.append({"name": k[5:], "temp": None})
    def toint(key, default=None):
        try:
            return int(st.get(key, default))
        except (TypeError, ValueError):
            return default
    def tofloat(key, default=None):
        try:
            return float(st.get(key, default))
        except (TypeError, ValueError):
            return default
    return {
        "cpu": tofloat("temp"),
        "gpu": tofloat("gpu"),
        "disks": disks,
        "fan_state": toint("fan_state", 0),
        "fan_target": toint("fan_target", 0),
        "mode": st.get("mode", "auto"),
        "ts": toint("ts", 0),
    }


def read_history():
    """解析历史 CSV: ts,cpu,gpu,d1,d2,d3,target → 按序列分组"""
    series = {"cpu": [], "gpu": [], "d1": [], "d2": [], "d3": [], "target": []}
    try:
        with open(HISTORY, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                parts = line.strip().split(",")
                if len(parts) < 7:
                    continue
                try:
                    t = int(parts[0])
                except ValueError:
                    continue
                for key, raw in zip(("cpu", "gpu", "d1", "d2", "d3", "target"), parts[1:7]):
                    if raw == "":
                        continue
                    try:
                        series[key].append([t, float(raw) if key != "target" else int(float(raw))])
                    except ValueError:
                        continue
    except OSError:
        pass
    return series


def write_conf(updates):
    """更新配置文件中的指定 key（保留其他行与注释）"""
    try:
        with open(CONF, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return False, "配置文件不存在"
    done = set()
    for i, line in enumerate(lines):
        m = re.match(r"^([A-Z_]+)=", line)
        if m and m.group(1) in updates:
            comment = ""
            if "#" in line:
                comment = "  # " + line.split("#", 1)[1].strip()
            lines[i] = "%s=%s%s\n" % (m.group(1), updates[m.group(1)], comment)
            done.add(m.group(1))
    for k, v in updates.items():
        if k not in done:
            lines.append("%s=%s\n" % (k, v))
    tmp = CONF + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.writelines(lines)
        os.replace(tmp, CONF)
    except OSError as e:
        return False, str(e)
    return True, "ok"


HTML_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>OESP风扇助手</title>
<style>
:root{
  --bg:#eef1f7; --panel:#ffffff; --panel2:#f7f9fd;
  --line:rgba(15,30,60,.09);
  --txt:#1e293b; --txt2:#64748b; --txt3:#94a3b8;
  --cyan:#0891b2; --blue:#2563eb; --green:#059669; --orange:#d97706; --red:#dc2626;
}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;color:var(--txt);
     padding:18px;min-height:100vh;background:
     radial-gradient(1100px 550px at 85% -10%,rgba(8,145,178,.07),transparent 60%),
     radial-gradient(900px 500px at -10% 15%,rgba(37,99,235,.07),transparent 55%),
     var(--bg);}
.wrap{max-width:1060px;margin:0 auto}
/* ---------- 头部 ---------- */
header{display:flex;align-items:center;justify-content:space-between;margin-bottom:18px;flex-wrap:wrap;gap:10px}
.brand{display:flex;align-items:center;gap:12px}
.logo{width:44px;height:44px;border-radius:13px;background:linear-gradient(135deg,#16233f,#0d1526);
      display:flex;align-items:center;justify-content:center;
      box-shadow:0 0 20px rgba(8,145,178,.22),inset 0 0 12px rgba(8,145,178,.12);
      border:1px solid rgba(8,145,178,.25)}
.logo svg{width:26px;height:26px}
h1{font-size:20px;font-weight:700;letter-spacing:.5px}
.subtitle{font-size:12px;color:var(--txt3);margin-top:3px}
.actions{display:flex;gap:10px;align-items:center}
.pill{display:flex;align-items:center;gap:8px;padding:9px 16px;border-radius:12px;font-size:13px;
      font-weight:600;background:var(--panel);border:1px solid var(--line);color:var(--txt2)}
.pill .dot{width:8px;height:8px;border-radius:50%;background:#94a3b8}
.pill.on{color:var(--green);border-color:rgba(5,150,105,.35);box-shadow:0 0 16px rgba(5,150,105,.14)}
.pill.on .dot{background:var(--green);box-shadow:0 0 9px var(--green);animation:pulse 1.6s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
.btn{padding:9px 16px;border-radius:12px;font-size:13px;font-weight:600;background:var(--panel);
     border:1px solid var(--line);color:var(--txt2);cursor:pointer;display:flex;align-items:center;gap:7px;transition:.2s}
.btn:hover{color:var(--cyan);border-color:rgba(8,145,178,.4);box-shadow:0 0 14px rgba(8,145,178,.15)}
.btn svg{width:14px;height:14px}
.btn.spinning svg{animation:spin .7s linear}
/* ---------- 卡片 ---------- */
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px;margin-bottom:14px}
.card{background:linear-gradient(160deg,var(--panel),var(--panel2));border:1px solid var(--line);
      border-radius:16px;padding:16px 18px;position:relative;overflow:hidden;
      box-shadow:0 2px 12px rgba(15,30,60,.06)}
.card::before{content:'';position:absolute;top:0;left:8%;right:8%;height:1px;
      background:linear-gradient(90deg,transparent,rgba(8,145,178,.4),transparent)}
.c-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}
.c-name{font-size:13px;color:var(--txt2);font-weight:600}
.c-ico{width:20px;height:20px;opacity:.75;flex:none}
.big{font-size:30px;font-weight:800;letter-spacing:.5px;font-variant-numeric:tabular-nums}
.big small{font-size:14px;color:var(--txt3);font-weight:500}
.lbl{font-size:12px;color:var(--txt3);margin-bottom:3px}
.row-flex{display:flex;justify-content:space-between;align-items:center;gap:8px}
/* 环形仪表 */
.ring-wrap{position:relative;width:74px;height:74px;flex:none}
.ring{width:74px;height:74px;transform:rotate(-90deg)}
.ring .bg{fill:none;stroke:rgba(15,30,60,.08);stroke-width:7}
.ring .val{fill:none;stroke-width:7;stroke-linecap:round;transition:stroke-dashoffset .6s ease,stroke .6s ease}
.ring-txt{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;font-size:13px;font-weight:700}
/* 风扇卡 */
.fan-box{display:flex;align-items:center;gap:16px}
.fan-svg{width:64px;height:64px;flex:none}
.fan-svg.spin{filter:drop-shadow(0 0 10px rgba(8,145,178,.4))}
.fan-rotor{transform-origin:24px 24px}
.fan-svg.spin .fan-rotor{animation:spin var(--spd,1s) linear infinite}
.fan-svg:not(.spin) .fan-rotor{opacity:.4}
@keyframes spin{to{transform:rotate(360deg)}}
/* ---------- 面板 ---------- */
.panel{background:linear-gradient(160deg,var(--panel),var(--panel2));border:1px solid var(--line);
       border-radius:16px;padding:16px 18px;margin-bottom:14px;position:relative;overflow:hidden;
       box-shadow:0 2px 12px rgba(15,30,60,.06)}
.panel::before{content:'';position:absolute;top:0;left:8%;right:8%;height:1px;
       background:linear-gradient(90deg,transparent,rgba(8,145,178,.4),transparent)}
.panel h2{font-size:15px;margin-bottom:12px;display:flex;align-items:center;gap:9px}
.panel h2 .bar{width:4px;height:15px;border-radius:2px;background:linear-gradient(180deg,var(--cyan),var(--blue));
       box-shadow:0 0 9px rgba(8,145,178,.6)}
#chart{width:100%;height:300px}
.legend{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}
.chip{display:flex;align-items:center;gap:7px;padding:5px 13px;border-radius:20px;font-size:12px;cursor:pointer;
      border:1px solid var(--line);color:var(--txt);background:rgba(15,30,60,.04);transition:.15s;user-select:none}
.chip .dot{width:9px;height:9px;border-radius:50%;box-shadow:0 0 6px currentColor}
.chip.off{opacity:.32}
.hint{font-size:12px;color:var(--txt3);margin-top:8px}
/* ---------- 表单 ---------- */
form{display:flex;flex-wrap:wrap;gap:12px;align-items:flex-end}
.field{flex:1 1 150px;min-width:140px}
.field label{display:block;font-size:12px;color:var(--txt2);margin-bottom:6px}
.field input{width:100%;padding:10px 12px;border:1px solid var(--line);border-radius:10px;background:#f8fafc;
      color:var(--txt);font-size:15px;outline:none;transition:.2s;font-variant-numeric:tabular-nums}
.field input:focus{border-color:var(--cyan);box-shadow:0 0 0 3px rgba(8,145,178,.12),0 0 12px rgba(8,145,178,.18)}
/* 运行模式分段选择器 */
.seg{display:flex;gap:6px;background:rgba(15,30,60,.05);padding:4px;border-radius:12px;border:1px solid var(--line)}
.seg button{flex:1;padding:9px 10px;border:0;border-radius:9px;background:transparent;color:var(--txt2);
      font-size:13px;font-weight:600;cursor:pointer;transition:.18s;font-family:inherit;white-space:nowrap}
.seg button:hover{color:var(--cyan)}
.seg button.act{background:linear-gradient(135deg,var(--cyan),var(--blue));color:#03121c;
      box-shadow:0 3px 14px rgba(56,190,255,.35)}
.seg button.act.hot{background:linear-gradient(135deg,#f59e0b,#dc2626);color:#fff;
      box-shadow:0 3px 14px rgba(220,38,38,.35)}
.seg button:disabled{opacity:.55;cursor:progress}
.seg button.busy{position:relative;color:transparent!important}
.seg button.busy::after{content:'';position:absolute;inset:0;margin:auto;width:13px;height:13px;
      border:2px solid rgba(8,145,178,.3);border-top-color:var(--cyan);border-radius:50%;
      animation:spin .6s linear infinite}
.seg button.act.busy::after{border-color:rgba(3,18,28,.28);border-top-color:#03121c}
.seg button.act.hot.busy::after{border-color:rgba(255,255,255,.35);border-top-color:#fff}
.save{flex:none;margin-left:auto;padding:11px 26px;border:0;border-radius:12px;font-size:14px;
      font-weight:700;color:#03121c;background:linear-gradient(135deg,var(--cyan),var(--blue));cursor:pointer;
      box-shadow:0 4px 20px rgba(56,190,255,.35);transition:.2s}
.save:hover{transform:translateY(-1px);box-shadow:0 7px 26px rgba(56,190,255,.5)}
.save:active{transform:translateY(0)}
#msg{font-size:13px;margin-top:10px;min-height:18px}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="brand">
      <div class="logo" id="logoFan"></div>
      <div>
        <h1>OESP风扇助手</h1>
        <div class="subtitle">OES Plus · 温控风扇管理</div>
      </div>
    </div>
    <div class="actions">
      <span id="fanbadge" class="pill"><span class="dot"></span><span id="fanbadgeTxt">风扇已停止</span></span>
      <button class="btn" id="refreshBtn" onclick="manualRefresh(this)">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M20 12a8 8 0 1 1-2.3-5.6M20 4v4h-4"/></svg>刷新
      </button>
    </div>
  </header>

  <div class="cards" id="cards"></div>

  <div class="panel">
    <h2><span class="bar"></span>温度曲线（最近 12 小时）</h2>
    <canvas id="chart"></canvas>
    <div class="legend" id="legend"></div>
    <div class="hint" id="histinfo"></div>
  </div>

  <div class="panel">
    <h2><span class="bar"></span>温控阈值设置</h2>
    <form id="cfgform" onsubmit="return saveCfg(event)">
      <div class="field" style="flex:1 1 100%;margin-bottom:14px">
        <label>风扇运行模式（点击即生效，无需保存）</label>
        <div class="seg" id="modeseg">
          <button type="button" data-mode="auto">自动温控</button>
          <button type="button" data-mode="on">常开 · 一直运行</button>
          <button type="button" data-mode="off">常关 · 一直停止</button>
        </div>
        <div style="font-size:12px;color:var(--txt3);margin-top:7px" id="modehint"></div>
      </div>
      <div class="field"><label>CPU 开启温度（℃）</label><input id="TEMP_ON" type="number" min="20" max="90"></div>
      <div class="field"><label>CPU 关闭温度（℃）</label><input id="TEMP_OFF" type="number" min="10" max="85"></div>
      <div class="field"><label>硬盘开启温度（℃）</label><input id="HDD_ON_TEMP" type="number" min="30" max="70"></div>
      <div class="field"><label>硬盘通风时间（秒）</label><input id="HDD_RUN_SECONDS" type="number" min="30" max="1800"></div>
      <button class="save" type="submit">保存温度设置</button>
    </form>
    <div id="msg"></div>
  </div>
</div>

<script>
const COLORS = { cpu:'#2563eb', gpu:'#9333ea', d1:'#059669', d2:'#d97706', d3:'#db2777' };
const NAMES  = { cpu:'CPU', gpu:'GPU', d1:'硬盘1', d2:'硬盘2', d3:'硬盘3' };
const MODE_TXT = { auto:'自动温控', on:'常开 · 一直运行', off:'常关 · 一直停止' };
const MODE_HINT = {
  auto:'按 CPU / 硬盘温度自动启停（阈值见下方），过热紧急保护生效',
  on:'风扇将持续全速运行，忽略温度判定；适合夏季高温或硬盘密集场景',
  off:'风扇将持续停止，忽略温度判定；注意：此模式下过热紧急保护也不会触发，请留意散热风险'
};
let visible = { cpu:true, gpu:true, d1:true, d2:true, d3:true };
let historyData = null;
let diskLabels = [];
let lastDiskKey = '';
let lastCardsKey = '';

const BLADE = 'M24 24c-2-8 1.5-14 8-15c1 6.5-2 12-8 15z';
const FAN_G = `<path d="${BLADE}" fill="#0ea5e9"/><path d="${BLADE}" transform="rotate(120 24 24)" fill="#0ea5e9" opacity=".8"/><path d="${BLADE}" transform="rotate(240 24 24)" fill="#0ea5e9" opacity=".6"/>`;
document.getElementById('logoFan').innerHTML =
  `<svg viewBox="0 0 48 48"><g>${FAN_G}</g><circle cx="24" cy="24" r="3.4" fill="#0d1526" stroke="#0ea5e9" stroke-width="1.6"/></svg>`;

const ICO = {
  chip: '<svg class="c-ico" viewBox="0 0 24 24" fill="none" stroke="#2563eb" stroke-width="1.6" stroke-linecap="round"><rect x="6" y="6" width="12" height="12" rx="2"/><rect x="10" y="10" width="4" height="4"/><path d="M9 3v3M15 3v3M9 18v3M15 18v3M3 9h3M3 15h3M18 9h3M18 15h3"/></svg>',
  gpu:  '<svg class="c-ico" viewBox="0 0 24 24" fill="none" stroke="#9333ea" stroke-width="1.6" stroke-linecap="round"><rect x="3" y="5" width="18" height="12" rx="2"/><path d="M9 21h6M12 17v4"/></svg>',
  hdd:  '<svg class="c-ico" viewBox="0 0 24 24" fill="none" stroke="#059669" stroke-width="1.6" stroke-linecap="round"><rect x="3" y="7" width="18" height="10" rx="2"/><circle cx="16.5" cy="12" r="1.3" fill="#059669" stroke="none"/><path d="M6 12h6"/></svg>',
  clock:'<svg class="c-ico" viewBox="0 0 24 24" fill="none" stroke="#0ea5e9" stroke-width="1.6" stroke-linecap="round"><circle cx="12" cy="12" r="8.5"/><path d="M12 7v5l3.5 2"/></svg>',
  hddSm:'<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><rect x="3" y="7" width="18" height="10" rx="2"/><circle cx="16.5" cy="12" r="1.3" fill="currentColor" stroke="none"/><path d="M6 12h6"/></svg>'
};

function tColor(t){
  if (t === null || t === undefined || t <= 0) return '#94a3b8';
  if (t < 45) return '#0ea5e9';
  if (t < 60) return '#059669';
  if (t < 75) return '#d97706';
  return '#dc2626';
}
function hexA(hex,a){ const n=parseInt(hex.slice(1),16); return `rgba(${n>>16},${(n>>8)&255},${n&255},${a})`; }

/* ---------- 卡片渲染 ---------- */
function ringSvg(t){
  const C = 213.63;
  const pct = (t && t > 0) ? Math.min(t, 100) / 100 : 0;
  const color = tColor(t);
  const off = (C * (1 - pct)).toFixed(1);
  const pctTxt = (t && t > 0) ? Math.round(pct * 100) + '%' : '--';
  return `<div class="ring-wrap"><svg class="ring" viewBox="0 0 80 80">
    <circle class="bg" cx="40" cy="40" r="34"/>
    <circle class="val" cx="40" cy="40" r="34" stroke="${color}" stroke-dasharray="${C}" stroke-dashoffset="${off}" style="filter:drop-shadow(0 0 4px ${color})"/>
  </svg><div class="ring-txt" style="color:${color}">${pctTxt}</div></div>`;
}
function tempCard(name, t, icon){
  const v = (t && t > 0) ? t : null;
  return `<div class="card">
    <div class="c-head"><span class="c-name">${name}</span>${icon}</div>
    <div class="row-flex">
      <div><div class="lbl">温度</div><div class="big" style="color:${tColor(t)}">${v === null ? '--' : v.toFixed(1)}<small>${v === null ? '' : ' ℃'}</small></div></div>
      ${ringSvg(t)}
    </div></div>`;
}
function fanCard(on, mode){
  const spd = on ? '0.6s' : '1s';
  const mtag = mode === 'on' ? '<span style="color:#dc2626;font-weight:700">常开</span>'
             : mode === 'off' ? '<span style="color:#64748b;font-weight:700">常关</span>'
             : '<span style="color:#0891b2;font-weight:700">自动</span>';
  return `<div class="card">
    <div class="c-head"><span class="c-name">风扇状态</span>
      <svg class="c-ico" viewBox="0 0 24 24" fill="none" stroke="${on ? '#059669' : '#94a3b8'}" stroke-width="1.6" stroke-linecap="round"><path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M18.4 5.6l-2.1 2.1M7.7 16.3l-2.1 2.1"/></svg></div>
    <div class="fan-box">
      <svg class="fan-svg ${on ? 'spin' : ''}" viewBox="0 0 48 48">
        <circle cx="24" cy="24" r="21.5" fill="none" stroke="rgba(8,145,178,.22)" stroke-width="1.5"/>
        <g class="fan-rotor" style="--spd:${spd}">${FAN_G}</g>
        <circle cx="24" cy="24" r="3.2" fill="#ffffff" stroke="#0ea5e9" stroke-width="1.5"/>
      </svg>
      <div><div class="lbl">当前状态</div>
      <div class="big" style="color:${on ? '#059669' : '#94a3b8'}">${on ? '运行中' : '已停止'}</div>
      <div class="lbl" style="margin-top:7px">模式 ${mtag}</div></div>
    </div></div>`;
}
function diskCards(disks){
  // 整行拉长：硬盘（平均）+ sda/sdb/sdc 各占一张卡片横排；额外识别到的盘追加；无数据/未插盘显示"空"
  const slots = ['sda', 'sdb', 'sdc'];
  const byName = {};
  disks.forEach(d => { byName[d.name] = d.temp; });
  disks.forEach(d => { if (!slots.includes(d.name)) slots.push(d.name); });
  const valid = slots.map(n => byName[n]).filter(t => t > 0);
  const avg = valid.length ? valid.reduce((a, b) => a + b, 0) / valid.length : null;
  let html = tempCard('硬盘（平均）', avg, ICO.hdd);
  html += slots.map(name => {
    const t = byName[name];
    if (t !== undefined && t !== null && t > 0) return tempCard(name, t, ICO.hdd);
    return `<div class="card">
      <div class="c-head"><span class="c-name">${name}</span>${ICO.hdd}</div>
      <div class="row-flex">
        <div><div class="lbl">温度</div><div class="big" style="color:#cbd5e1">空</div></div>
        ${ringSvg(null)}
      </div></div>`;
  }).join('');
  return html;
}
function clockCard(samples){
  return `<div class="card">
    <div class="c-head"><span class="c-name">系统时间</span>${ICO.clock}</div>
    <div class="lbl" id="clockDate">--</div>
    <div class="big" id="clockTime" style="color:var(--txt)">--:--:--</div>
    <div class="hint" style="margin-top:8px">已采样 ${samples} 点 · 约每 1 分钟一个点</div></div>`;
}
function tickClock(){
  const d = new Date();
  const t = document.getElementById('clockTime'), dt = document.getElementById('clockDate');
  if (t) t.textContent = [d.getHours(), d.getMinutes(), d.getSeconds()].map(x => String(x).padStart(2, '0')).join(':');
  if (dt) dt.textContent = `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日`;
}
setInterval(tickClock, 1000);

/* ---------- 数据刷新 ---------- */
async function refreshState(){
  try {
    const r = await fetch('api/state'); const s = await r.json();
    const b = document.getElementById('fanbadge');
    if (s.fan_state === 1) { b.className = 'pill on'; document.getElementById('fanbadgeTxt').textContent = '风扇运行中'; }
    else { b.className = 'pill'; document.getElementById('fanbadgeTxt').textContent = '风扇已停止'; }
    diskLabels = s.disks.map(d => d.name);
    // 硬盘列表变化时重绘：图例要按实际盘位显示，否则首次加载只会有 CPU/GPU 两个图例
    const dk = diskLabels.join(',');
    if (dk !== lastDiskKey) { lastDiskKey = dk; if (historyData) drawChart(); }
    const samples = historyData ? historyData.cpu.length : 0;
    const key = JSON.stringify([s.cpu, s.gpu, s.disks, s.fan_state, s.fan_target, s.mode, samples]);
    if (key !== lastCardsKey) {
      lastCardsKey = key;
      document.getElementById('cards').innerHTML =
        clockCard(samples) +
        fanCard(s.fan_state === 1, s.mode) +
        tempCard('CPU', s.cpu, ICO.chip) +
        tempCard('GPU', s.gpu, ICO.gpu) +
        diskCards(s.disks);
      tickClock();
    }
    if (s.conf && !document.activeElement.matches('input')) {
      for (const k of ['TEMP_ON', 'TEMP_OFF', 'HDD_ON_TEMP', 'HDD_RUN_SECONDS']) {
        const el = document.getElementById(k);
        if (el && s.conf[k] !== undefined) el.value = s.conf[k];
      }
      if (s.conf.FAN_MODE && !modeBusy) { curMode = s.conf.FAN_MODE; paintMode(); }
    }
  } catch(e) {}
}

async function refreshHistory(){
  try {
    const r = await fetch('api/history'); historyData = await r.json();
    lastCardsKey = '';  // 采样数可能变化，强制重渲染卡片
    drawChart();
    const n = historyData.cpu.length;
    document.getElementById('histinfo').textContent =
      n ? `共 ${n} 个采样点 · 约每 1 分钟一个点 · 保留最近 12 小时` : '暂无历史数据，等待守护进程采样…';
  } catch(e) {}
}

function manualRefresh(btn){
  btn.classList.add('spinning');
  setTimeout(() => btn.classList.remove('spinning'), 700);
  refreshState(); refreshHistory();
}

/* ---------- 曲线 ---------- */
function drawChart(){
  const cv = document.getElementById('chart');
  const dpr = window.devicePixelRatio || 1;
  const W = cv.clientWidth, H = cv.clientHeight;
  cv.width = W * dpr; cv.height = H * dpr;
  const ctx = cv.getContext('2d'); ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, W, H);
  if (!historyData || !historyData.cpu.length) {
    ctx.fillStyle = '#94a3b8'; ctx.font = '14px sans-serif'; ctx.textAlign = 'center';
    ctx.fillText('暂无数据', W / 2, H / 2); return;
  }
  const padL = 40, padR = 12, padT = 12, padB = 26;
  const plotW = W - padL - padR, plotH = H - padT - padB;
  const all = [];
  for (const k of ['cpu', 'gpu', 'd1', 'd2', 'd3']) if (visible[k]) all.push(...historyData[k]);
  if (!all.length) return;
  const tMin = all[0][0], tMax = all[all.length - 1][0];
  let vMax = 0; all.forEach(p => { if (p[1] > vMax) vMax = p[1]; });
  vMax = Math.max(30, Math.ceil(vMax / 10) * 10 + 5);
  const X = t => padL + (tMax === tMin ? 0 : (t - tMin) / (tMax - tMin) * plotW);
  const Y = v => padT + plotH - (v / vMax) * plotH;

  // 网格 + 刻度
  ctx.strokeStyle = 'rgba(15,30,60,.07)'; ctx.fillStyle = '#94a3b8';
  ctx.font = '11px sans-serif'; ctx.textAlign = 'right'; ctx.lineWidth = 1;
  for (let v = 0; v <= vMax; v += 10) {
    ctx.beginPath(); ctx.moveTo(padL, Y(v)); ctx.lineTo(W - padR, Y(v)); ctx.stroke();
    ctx.fillText(v + '°', padL - 7, Y(v) + 4);
  }
  ctx.textAlign = 'center';
  const span = tMax - tMin, step = span > 12 * 3600 ? 3 * 3600 : (span > 4 * 3600 ? 3600 : 900);
  for (let t = tMin - (tMin % step); t <= tMax; t += step) {
    if (t < tMin) continue;
    const d = new Date(t * 1000);
    const lbl = String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
    ctx.fillText(lbl, X(t), H - 8);
  }
  // 序列（辉光线条 + 渐变面积）
  for (const k of ['cpu', 'gpu', 'd1', 'd2', 'd3']) {
    if (!visible[k]) continue;
    const pts = historyData[k]; if (pts.length < 2) continue;
    const color = COLORS[k];
    // 面积渐变
    const g = ctx.createLinearGradient(0, padT, 0, H - padB);
    g.addColorStop(0, hexA(color, .16)); g.addColorStop(1, hexA(color, 0));
    ctx.beginPath();
    pts.forEach((p, i) => { i === 0 ? ctx.moveTo(X(p[0]), Y(p[1])) : ctx.lineTo(X(p[0]), Y(p[1])); });
    ctx.lineTo(X(pts[pts.length - 1][0]), H - padB); ctx.lineTo(X(pts[0][0]), H - padB); ctx.closePath();
    ctx.fillStyle = g; ctx.fill();
    // 辉光线
    ctx.save();
    ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.lineJoin = 'round';
    ctx.shadowColor = color; ctx.shadowBlur = 7;
    ctx.beginPath();
    pts.forEach((p, i) => { i === 0 ? ctx.moveTo(X(p[0]), Y(p[1])) : ctx.lineTo(X(p[0]), Y(p[1])); });
    ctx.stroke(); ctx.restore();
  }
  renderLegend();
}

function renderLegend(){
  const lg = document.getElementById('legend');
  lg.innerHTML = '';
  for (const k of ['cpu', 'gpu', 'd1', 'd2', 'd3']) {
    if (k[0] === 'd' && !diskLabels[parseInt(k[1]) - 1]) continue;  // 该盘未检测到 / 无数据，不显示图例
    const label = (k[0] === 'd' && diskLabels[parseInt(k[1]) - 1]) ? diskLabels[parseInt(k[1]) - 1] : NAMES[k];
    const chip = document.createElement('span');
    chip.className = 'chip' + (visible[k] ? '' : ' off');
    chip.style.color = COLORS[k];
    chip.innerHTML = `<span class="dot" style="background:${COLORS[k]}"></span><span style="color:var(--txt2)">${label}</span>`;
    chip.onclick = () => { visible[k] = !visible[k]; drawChart(); };
    lg.appendChild(chip);
  }
}

/* ---------- 运行模式：点击立即生效（无需保存按钮） ---------- */
let curMode = 'auto';
let modeBusy = false;

function paintMode(){
  document.querySelectorAll('#modeseg button').forEach(b => {
    const on = b.dataset.mode === curMode;
    b.classList.toggle('act', on);
    b.classList.toggle('hot', on && curMode !== 'auto');
  });
  const h = document.getElementById('modehint');
  if (h) h.textContent = MODE_HINT[curMode] || '';
}

function setMsg(text, color){
  const msg = document.getElementById('msg');
  if (!msg) return;
  msg.style.color = color; msg.textContent = text;
}

async function applyMode(mode, btn){
  if (modeBusy || mode === curMode) return;
  const prev = curMode;
  modeBusy = true;
  curMode = mode; paintMode();                      // 乐观更新，点击即反馈
  const btns = document.querySelectorAll('#modeseg button');
  btns.forEach(b => { b.disabled = true; if (b === btn) b.classList.add('busy'); });
  setMsg('正在切换到「' + MODE_TXT[mode] + '」…', '#64748b');
  try {
    const r = await fetch('api/config', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ FAN_MODE: mode })       // 只提交模式，不动温度阈值
    });
    const j = await r.json();
    if (j.ok) {
      setMsg('✓ 已切换为「' + MODE_TXT[mode] + '」，守护进程 2 秒内生效', '#059669');
      refreshState();                              // 立即回读一次真实状态
      setTimeout(refreshState, 2300);              // 守护进程生效后再回读，刷新风扇徽标
    } else {
      curMode = prev; paintMode();
      setMsg('切换失败：' + (j.error || ''), '#dc2626');
    }
  } catch(e) {
    curMode = prev; paintMode();
    setMsg('切换失败：' + e, '#dc2626');
  } finally {
    modeBusy = false;
    btns.forEach(b => { b.disabled = false; b.classList.remove('busy'); });
  }
}
document.querySelectorAll('#modeseg button').forEach(b => {
  b.onclick = () => applyMode(b.dataset.mode, b);
});
paintMode();

/* ---------- 保存温度设置 ---------- */
async function saveCfg(ev){
  ev.preventDefault();
  const body = { FAN_MODE: curMode };
  for (const k of ['TEMP_ON', 'TEMP_OFF', 'HDD_ON_TEMP', 'HDD_RUN_SECONDS'])
    body[k] = parseInt(document.getElementById(k).value, 10);
  if (body.TEMP_ON <= body.TEMP_OFF) { setMsg('CPU 开启温度必须高于关闭温度', '#dc2626'); return false; }
  try {
    const r = await fetch('api/config', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body) });
    const j = await r.json();
    if (j.ok) { setMsg('✓ 温度设置已保存，守护进程 2 秒内生效', '#059669'); refreshState(); }
    else { setMsg('保存失败：' + (j.error || ''), '#dc2626'); }
  } catch(e) { setMsg('保存失败：' + e, '#dc2626'); }
  return false;
}

window.addEventListener('resize', drawChart);
refreshState(); refreshHistory();
setInterval(refreshState, 5000);
setInterval(refreshHistory, 60000);
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _norm(self, raw):
        """剥离网关前缀；裸前缀重定向到带斜杠形式，保证相对路径解析正确"""
        path = raw.split("?", 1)[0]
        if BASE:
            if path == BASE:
                return None  # 触发 301
            if path.startswith(BASE + "/"):
                path = path[len(BASE):]
        return path

    def do_GET(self):
        path = self._norm(self.path)
        if path is None:
            self.send_response(301)
            self.send_header("Location", BASE + "/")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/" or path == "/index.html":
            body = HTML_PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/state":
            st = read_state()
            conf = read_kv_file(CONF)
            st["conf"] = {k: conf.get(k) for k in EDITABLE if k in conf}
            st["conf"].update({k: conf.get(k) for k in EDITABLE_STR if k in conf})
            st["server_time"] = int(time.time())
            self._json(st)
        elif path == "/api/history":
            self._json(read_history())
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        path = self._norm(self.path)
        if path is None:
            self.send_response(301)
            self.send_header("Location", BASE + "/")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path != "/api/config":
            self._json({"error": "not found"}, 404)
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except (ValueError, json.JSONDecodeError):
            self._json({"ok": False, "error": "请求格式错误"}, 400)
            return
        updates = {}
        for k, (lo, hi) in EDITABLE.items():
            if k in data:
                try:
                    v = int(data[k])
                except (TypeError, ValueError):
                    self._json({"ok": False, "error": "%s 必须是整数" % k}, 400)
                    return
                if not (lo <= v <= hi):
                    self._json({"ok": False, "error": "%s 超出范围 %d-%d" % (k, lo, hi)}, 400)
                    return
                updates[k] = v
        for k, allowed in EDITABLE_STR.items():
            if k in data:
                v = str(data[k]).strip().lower()
                if v not in allowed:
                    self._json({"ok": False, "error": "%s 只能是 %s" % (k, "/".join(allowed))}, 400)
                    return
                updates[k] = v
        if not updates:
            self._json({"ok": False, "error": "没有可更新的字段"}, 400)
            return
        # 阈值交叉校验：合并「本次提交 + 现有配置」后判断，避免只改一个字段时漏检
        if "TEMP_ON" in updates or "TEMP_OFF" in updates:
            cur = read_kv_file(CONF)
            try:
                eff_on = int(updates.get("TEMP_ON", cur.get("TEMP_ON", 0)))
                eff_off = int(updates.get("TEMP_OFF", cur.get("TEMP_OFF", 0)))
            except (TypeError, ValueError):
                self._json({"ok": False, "error": "温度阈值必须是整数"}, 400)
                return
            if eff_on <= eff_off:
                self._json({"ok": False, "error": "CPU 开启温度必须高于关闭温度（当前提交后为 %d / %d）" % (eff_on, eff_off)}, 400)
                return
        ok, msg = write_conf(updates)
        self._json({"ok": ok, "error": None if ok else msg},
                   200 if ok else 500)

    def log_message(self, *args):
        pass  # 静默访问日志


class UnixHTTPServer(ThreadingHTTPServer):
    """监听 Unix Socket 的 HTTP 服务（供 fnOS 统一网关反代）"""
    address_family = getattr(socket, "AF_UNIX", socket.AF_INET)

    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        self.server_name = "localhost"
        self.server_port = 0

    def get_request(self):
        request, _ = self.socket.accept()
        return request, ("local", 0)


if __name__ == "__main__":
    if SOCK and hasattr(socket, "AF_UNIX"):
        try:
            os.unlink(SOCK)
        except OSError:
            pass
        os.makedirs(os.path.dirname(SOCK), exist_ok=True)
        srv = UnixHTTPServer(SOCK, Handler)
        os.chmod(SOCK, 0o666)
        print("OESP风扇助手 Web 监控: unix:%s (网关前缀 %s)" % (SOCK, BASE or "/"), flush=True)
    else:
        if SOCK:
            print("警告: 当前平台不支持 Unix Socket，回退 TCP 端口 %d" % PORT, flush=True)
        srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
        print("OESP风扇助手 Web 监控: http://0.0.0.0:%d" % PORT, flush=True)
    srv.serve_forever()
