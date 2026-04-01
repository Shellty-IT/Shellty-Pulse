"""
Shellty Pulse — Service Health Monitor

A tool for monitoring the availability of web services.
Also serves as a keep-alive mechanism for backends on Render Free Tier
(which go to sleep after 15 minutes of inactivity).

Features:
    - Pings registered URLs every X minutes (configurable from dashboard)
    - Measures response time and records status
    - Displays an HTML dashboard on the main page (dark theme, responsive)
    - Provides REST API for service management
    - Configurable ping interval: 10 min / 15 min / 30 min / 1 hour
    - Frontend URL linking for each monitored service

Author: Shellty IT
"""

# ============================================
# Imports
# ============================================
import os
import json
import uuid
import time
import logging
import threading
from datetime import datetime, timezone

import requests as http_requests
from flask import Flask, jsonify, request, render_template_string
from apscheduler.schedulers.background import BackgroundScheduler

# ============================================
# Logging Configuration
# ============================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("shellty-pulse")

# ============================================
# Configuration from Environment Variables
# ============================================
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", 10))
SERVICES_JSON = os.environ.get("SERVICES", "[]")

# Mutable ping interval — can be changed via API at runtime
ping_interval: int = int(os.environ.get("PING_INTERVAL", 600))

# Available intervals for the dashboard selector (seconds → label)
AVAILABLE_INTERVALS = {
    600: "10 min",
    900: "15 min",
    1800: "30 min",
    3600: "1 hour",
}

# Maximum allowed lengths for user input
MAX_NAME_LENGTH = 100
MAX_URL_LENGTH = 2048

# ============================================
# In-Memory Data Store
# ============================================
services: list[dict] = []
services_lock = threading.Lock()
auto_ping_enabled: bool = True
last_check_time: str | None = None

# Global scheduler reference (initialized in start_app)
scheduler: BackgroundScheduler | None = None

# ============================================
# Dashboard HTML Template
# ============================================
DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Shellty Pulse</title>
    <link rel="icon" type="image/svg+xml" sizes="any" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='220 270 580 460' width='128' height='128'><g><path fill='%2300a6fe' d='M291.29 270.28C314.84 258.89 338.4 247.49 361.99 236.19C399.13 254.75 436.31 273.21 473.65 291.37C480.63 295.01 487.67 298.51 494.63 302.19C486.5 323.39 479.07 344.86 471.05 366.11C462.37 390.01 453.2 413.72 444.63 437.66C437.29 437.33 429.89 437.95 422.6 437.33C399.27 420.67 376.07 403.78 352.43 387.56C324.66 367.98 296.79 348.54 269.02 328.96C255.58 319.9 242.6 310.17 229.09 301.21C249.61 290.48 270.58 280.64 291.29 270.28Z'/><path fill='%2300a6fe' d='M658.95 237.93C662.07 235.76 665.39 238.5 668.35 239.6C707.6 258.21 746.43 277.69 785.61 296.45C788.78 297.94 791.98 299.36 795.03 301.12C788.17 306.29 780.81 310.76 773.9 315.86C756.57 327.82 739.41 340.03 722.12 352.04C709.17 360.71 696.63 369.98 683.73 378.7C656.85 397.44 630.12 416.4 603.51 435.52C600.95 437.28 598.56 439.5 595.55 440.44C590.51 440.88 585.43 440.42 580.38 440.64C576.55 429.85 572.36 419.2 568.73 408.34C561.03 387.39 553.26 366.48 545.57 345.53C540.38 330.93 535.12 316.36 529.56 301.91C532.05 300.12 534.89 298.95 537.63 297.62C562.92 284.74 588.6 272.65 614.06 260.11C628.97 252.61 644.11 245.57 658.95 237.93Z'/></g><g><path fill='%230086f2' d='M796.44 316.46C799.22 314.73 801.61 312.35 804.69 311.12C810.33 335.05 815.44 359.11 820.87 383.09C824.29 398.05 827.25 413.11 830.87 428.02C832.86 438.03 834.8 448.09 837.7 457.89C833.46 457.63 829.23 457.62 825 457.65C772.67 457.66 720.33 457.64 668 457.67C665.17 457.61 662.3 457.97 659.52 457.39C656.04 455.33 653.35 452.21 650.1 449.85C645.3 446.34 640.71 442.57 636.1 438.84C633.41 436.63 630.42 434.78 628.05 432.21C640.51 422.89 653.2 413.85 666.01 405.02C697.7 382.57 729.79 360.69 762.06 339.09C773.7 331.82 784.92 323.9 796.44 316.46Z'/><path fill='%230086f2' d='M218.51 316.58C218.82 314.93 219.18 313.26 219.96 311.76C241.46 326.41 263.34 340.51 284.85 355.15C307.33 370.29 329.88 385.35 352.06 400.93C366.79 411.22 381.43 421.65 396.05 432.09C394.92 433.27 393.78 434.46 392.5 435.49C384.5 441.81 376.39 448 368.44 454.4C366.92 455.55 365.41 456.82 363.59 457.49C359.75 457.98 355.86 457.56 351.99 457.65C297.4 457.73 242.81 457.52 188.22 457.75C189.73 452.16 190.55 446.42 191.88 440.79C196.74 417.37 201.75 393.98 206.77 370.59C207.13 368.88 207.96 367.33 208.39 365.64C210.82 349.11 215.29 332.97 218.51 316.58Z'/></g><g><path fill='%2325efff' d='M512.33 325.1C512.65 325.2 513.28 325.4 513.59 325.51C520.49 352.92 527.41 380.35 534.19 407.8C540.73 435.78 548.05 463.57 554.41 491.6C556.68 499.98 558.49 508.47 560.31 516.96C561.88 514.43 562.66 511.54 563.86 508.84C569.31 496.53 574.45 484.09 579.8 471.73C580.45 470.46 580.95 468.94 582.22 468.13C586.21 467.8 590.21 468.36 594.2 468.61C617.71 470.11 641.23 471.69 664.78 472.43C675.13 473.48 685.56 472.88 695.93 473.77C705.47 474.4 715.03 474.11 724.58 474.5C735.39 475.24 746.23 475.26 757.06 475.35C772.04 475.89 787.02 476.24 802.01 476.35C810.38 476.26 818.74 476.56 827.09 476.87C825.52 479.31 823.85 481.77 821.51 483.54C820.12 484.18 818.52 483.99 817.05 484.07C800.03 483.88 783.02 484.83 766.01 484.67C740.67 484.82 715.33 485.24 690 485.67C659.44 485.56 628.87 486.19 598.31 486.28C597.12 487.19 596.66 488.71 595.96 489.98C591.84 498.8 587.79 507.65 583.48 516.38C575.73 533.2 567.75 549.92 559.42 566.47C556.87 571.12 554.75 576 551.86 580.45C551.36 579.46 550.94 578.45 550.6 577.42C547.33 565.48 544.4 553.45 541.59 541.4C537.8 527.29 534.76 512.99 531.18 498.82C524.6 470.89 517.48 443.06 512.13 414.86C511.42 411.11 511.15 407.28 509.95 403.64C509.33 404.89 508.78 406.17 508.5 407.54C501.76 434.67 493.74 461.45 486.41 488.42C482.85 500.45 479.84 512.66 476.06 524.63C471.29 519.09 467.5 512.79 463.14 506.94C458.05 499.38 452.43 492.2 447.43 484.58C446.44 483.1 445.26 481.77 444.07 480.47C440.75 482.74 439.02 486.69 435.87 488.99C411.56 488.41 387.25 487.68 362.93 487.67C345 486.92 327.04 486.74 309.1 486.58C292.42 485.49 275.71 485.77 259.01 485.58C240.5 484.49 221.94 484.78 203.41 484.57C201.18 482.26 198.52 480.25 197.02 477.34C212.65 477.4 228.29 477.22 243.92 476.68C249.93 476.22 255.96 476.39 261.99 476.35C286.66 476.35 311.32 475.88 335.98 475.34C354 475.34 372.02 475.18 390.03 475.07C392.84 475.05 395.63 475.63 398.44 475.35C406.87 474.36 415.38 475.12 423.84 474.76C425.13 474.78 425.98 473.63 426.9 472.89C430.45 469.44 433.44 465.46 436.87 461.9C441.19 457.38 444.85 452.21 449.61 448.12C452.64 455.61 456.64 462.66 460.03 469.99C462.06 473.65 463.31 477.76 465.88 481.11C469.37 473.03 471.5 464.42 474.03 456C486.2 416.18 497.14 376 509.17 336.13C510.33 332.49 511.44 328.82 512.33 325.1Z'/></g><g><path fill='%230062dd' d='M773.07 501.83C784.13 501.17 795.22 500.91 806.3 500.45C805.88 501.27 805.4 502.05 804.88 502.79C789.35 519.95 773.38 536.7 757.69 553.69C748.03 564.36 738.13 574.81 728.29 585.32C721.47 593.15 713.85 600.23 706.92 607.95C702.99 612.34 698.73 616.41 694.64 620.65C692.58 622.75 690.67 625.04 688.25 626.77C672.78 611.22 656.4 596.6 640.72 581.26C631.56 573.12 623.08 564.25 613.93 556.1C610.05 552.65 606.46 548.88 602.53 545.48C601.45 544.59 600.57 543.5 599.9 542.29C612.41 531.02 624.31 519.09 636.77 507.77C639.53 506.27 642.99 507.1 646.04 506.66C655.68 505.71 665.4 506.12 675.06 505.33C679.36 504.97 683.68 504.98 688.01 504.97C693.34 504.98 698.64 504.17 703.97 504.07C727.02 503.89 750.03 502.37 773.07 501.83Z'/><path fill='%230062dd' d='M216.77 501.11C231.53 500.99 246.27 502 261.02 502.35C272.7 503.03 284.41 502.93 296.09 503.58C305.48 504.06 314.88 503.83 324.25 504.54C332.63 505.1 341.05 504.77 349.42 505.5C357.74 506.16 366.1 505.66 374.41 506.51C378.51 506.95 382.66 506.64 386.74 507.23C390.27 509.24 392.86 512.51 395.89 515.16C404.87 523.15 413.42 531.62 422.59 539.39C423.37 540.14 425.26 541.44 423.79 542.55C422.19 544.36 420.26 545.82 418.46 547.42C411.75 553.37 405.57 559.87 399.01 565.99C393.32 570.96 387.99 576.32 382.52 581.54C375.46 587.79 368.81 594.48 362.03 601.03C356.02 606.34 350.33 612 344.57 617.58C341.49 620.51 338.8 623.93 335.07 626.1C326.41 618.05 318.4 609.33 310.28 600.74C304.95 595.07 299.29 589.73 294.05 583.98C287.28 576.1 279.47 569.2 272.74 561.29C262.37 550.31 252.06 539.27 241.81 528.18C236.15 522.5 231.09 516.25 225.35 510.64C222.5 507.45 219.12 504.71 216.77 501.11Z'/></g><g><path fill='%230041ba' d='M428.71 555.73C430.79 553.81 432.65 551.59 435.08 550.1C437.56 550.97 439.31 553.11 441.32 554.72C456.17 567.49 470.61 580.74 485.6 593.36C491.85 599.16 498.57 604.44 504.96 610.11C506.05 611.26 507.69 612.33 507.71 614.11C508.3 644.72 507.63 675.35 508.65 705.95C508.6 726.94 508.83 747.94 509.01 768.94C493.87 755.7 478.08 743.23 462.74 730.23C438.37 710.3 414.43 689.85 389.98 670.02C375.17 657.29 359.75 645.26 345.17 632.27C357.29 621.01 369.64 610.02 381.76 598.76C397.29 584.28 413.38 570.42 428.71 555.73Z'/><path fill='%230041ba' d='M584.29 562.26C587.15 559.59 590.22 557.14 593.12 554.5C595.35 555.62 597.11 557.43 598.95 559.09C612.43 570.9 625.61 583.06 639.13 594.83C644.26 599.74 649.61 604.41 654.91 609.14C663.24 616.24 671.07 623.9 679.28 631.14C678.19 633.05 676.38 634.36 674.71 635.73C637.26 667.29 599.4 698.38 561.85 729.82C547.46 741.78 532.86 753.51 518.62 765.65C517.88 766.2 517.1 766.67 516.26 767.06C516.96 756.52 517.17 745.96 517.49 735.41C518.87 720.98 517.71 706.45 519 692.02C519.12 674.98 520 657.97 520.22 640.93C520.69 634.28 520.72 627.61 520.84 620.95C520.54 618.42 523.07 617.17 524.6 615.64C544.39 597.73 564.7 580.4 584.29 562.26Z'/></g></svg>">
    <style>
        /* === Reset & Base === */
        * { margin: 0; padding: 0; box-sizing: border-box; }

        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #0d1117;
            color: #c9d1d9;
            min-height: 100vh;
            padding: 2rem;
        }

        .container { max-width: 920px; margin: 0 auto; }

        /* === Header === */
        header {
            text-align: center;
            margin-bottom: 2rem;
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 0.2rem;
        }

        /* === Logo SVG Animation === */
        .logo-icon {
            width: 160px;
            height: 160px;
            display: block;
            animation: heartbeat 2s ease-in-out infinite;
            filter: drop-shadow(0 0 8px rgba(0, 166, 254, 0.4));
            transition: filter 0.3s ease, transform 0.3s ease;
            cursor: pointer;
        }

        .logo-icon:hover {
            filter: drop-shadow(0 0 12px rgba(37, 239, 255, 0.6))
            drop-shadow(0 0 24px rgba(0, 166, 254, 0.4));
            transform: scale(1.1);
        }

        /* Heartbeat animation — double-beat like real heart */
        @keyframes heartbeat {
            0%   { transform: scale(1); }
            15%  { transform: scale(1.1); }
            30%  { transform: scale(1); }
            45%  { transform: scale(1.06); }
            60%  { transform: scale(1); }
            100% { transform: scale(1); }
        }

        /* Pulse line glow animation — synced with heartbeat */
        .logo-icon #pulse-line path {
            animation: pulseGlow 2s ease-in-out infinite;
        }

        @keyframes pulseGlow {
            0%   { filter: drop-shadow(0 0 2px rgba(37, 239, 255, 0.3)); opacity: 0.6; }
            15%  { filter: drop-shadow(0 0 14px rgba(37, 239, 255, 1)) drop-shadow(0 0 28px rgba(37, 239, 255, 0.6)); opacity: 1; }
            30%  { filter: drop-shadow(0 0 2px rgba(37, 239, 255, 0.3)); opacity: 0.6; }
            45%  { filter: drop-shadow(0 0 10px rgba(37, 239, 255, 0.9)) drop-shadow(0 0 20px rgba(37, 239, 255, 0.5)); opacity: 1; }
            60%  { filter: drop-shadow(0 0 2px rgba(37, 239, 255, 0.3)); opacity: 0.6; }
            100% { filter: drop-shadow(0 0 2px rgba(37, 239, 255, 0.3)); opacity: 0.6; }
        }

        .logo-icon:hover #pulse-line path {
            animation: pulseGlowHover 0.6s ease-in-out infinite;
        }

        @keyframes pulseGlowHover {
            0%   { filter: drop-shadow(0 0 8px rgba(37, 239, 255, 0.7)); opacity: 0.9; }
            50%  { filter: drop-shadow(0 0 24px rgba(37, 239, 255, 1)) drop-shadow(0 0 40px rgba(37, 239, 255, 0.8)); opacity: 1; }
            100% { filter: drop-shadow(0 0 8px rgba(37, 239, 255, 0.7)); opacity: 0.9; }
        }

        header h1 {
            font-size: 2.2rem;
            color: #f0f6fc;
            margin: 0;
        }
         .brand-accent {
            color: #58a6ff;
        }

        .subtitle { color: #8b949e; font-size: 0.95rem; }

        /* === Overall Status Banner === */
        .overall-status {
            text-align: center;
            padding: 1rem 2rem;
            border-radius: 12px;
            margin-bottom: 1rem;
            font-size: 1.1rem;
            font-weight: 600;
            border: 1px solid #30363d;
        }

        .overall-status.operational { background: #0d1f0d; border-color: #238636; color: #3fb950; }
        .overall-status.degraded    { background: #1f1d0d; border-color: #9e6a03; color: #d29922; }
        .overall-status.slow        { background: #1f160d; border-color: #bd5a00; color: #db6d28; }
        .overall-status.down        { background: #1f0d0d; border-color: #da3633; color: #f85149; }
        .overall-status.unknown     { background: #161b22; border-color: #30363d; color: #8b949e; }

        /* === Status Legend === */
        .status-legend {
            display: flex;
            flex-wrap: wrap;
            gap: 0.75rem 1.25rem;
            justify-content: center;
            align-items: center;
            margin-bottom: 1.5rem;
            padding: 0.6rem 1rem;
            background: #161b22;
            border: 1px solid #30363d;
            border-radius: 10px;
            font-size: 0.8rem;
        }

        .legend-item {
            display: flex;
            align-items: center;
            gap: 0.35rem;
            color: #8b949e;
        }

        .legend-dot {
            width: 10px;
            height: 10px;
            border-radius: 50%;
            display: inline-block;
        }

        .legend-dot.operational { background: #3fb950; }
        .legend-dot.degraded    { background: #d29922; }
        .legend-dot.slow        { background: #db6d28; }
        .legend-dot.down        { background: #f85149; }
        .legend-dot.unknown     { background: #484f58; }

        .legend-desc { color: #6e7681; }

        /* === Control Buttons === */
        .controls {
            display: flex;
            flex-wrap: wrap;
            gap: 0.75rem;
            align-items: center;
            justify-content: center;
            margin-bottom: 1.5rem;
        }

        .btn {
            padding: 0.6rem 1.2rem;
            border: 1px solid #30363d;
            border-radius: 8px;
            background: #21262d;
            color: #c9d1d9;
            cursor: pointer;
            font-size: 0.9rem;
            transition: all 0.2s;
        }

        .btn:hover           { background: #30363d; border-color: #58a6ff; }
        .btn:disabled         { opacity: 0.5; cursor: not-allowed; }
        .btn.primary          { background: #238636; border-color: #238636; color: #fff; }
        .btn.primary:hover    { background: #2ea043; }
        .btn.active           { background: #1f6feb; border-color: #1f6feb; color: #fff; }
        .btn.inactive         { background: #21262d; border-color: #f85149; color: #f85149; }

        /* === Interval Selector === */
        .interval-selector {
            display: flex;
            align-items: center;
            gap: 0.4rem;
            flex-wrap: wrap;
        }

        .interval-label {
            color: #8b949e;
            font-size: 0.8rem;
            margin-right: 0.2rem;
        }

        .interval-btn {
            padding: 0.3rem 0.65rem;
            border: 1px solid #30363d;
            border-radius: 6px;
            background: #21262d;
            color: #8b949e;
            cursor: pointer;
            font-size: 0.78rem;
            transition: all 0.2s;
        }

        .interval-btn:hover         { border-color: #58a6ff; color: #c9d1d9; }
        .interval-btn.active         { background: #1f6feb; border-color: #1f6feb; color: #fff; }
        .interval-btn:disabled        { opacity: 0.4; cursor: not-allowed; }
        .interval-btn:disabled:hover  { border-color: #30363d; color: #8b949e; }

        .check-info {
            color: #8b949e;
            font-size: 0.85rem;
            text-align: center;
            width: 100%;
        }

        /* === Service Cards === */
        .services-grid {
            display: flex;
            flex-direction: column;
            gap: 0.75rem;
            margin-bottom: 2rem;
        }

        .service-card {
            display: flex;
            align-items: center;
            gap: 1rem;
            padding: 1rem 1.25rem;
            background: #161b22;
            border: 1px solid #30363d;
            border-radius: 10px;
            transition: border-color 0.2s;
        }

        .service-card:hover { border-color: #58a6ff; }
        .status-icon        { font-size: 1.5rem; flex-shrink: 0; }

        .service-info { flex: 1; min-width: 0; }

        .service-name {
            font-weight: 600;
            color: #f0f6fc;
            font-size: 1rem;
            margin-bottom: 0.15rem;
        }

        .service-name a {
            color: #f0f6fc;
            text-decoration: none;
            transition: color 0.2s;
        }

        .service-name a:hover { color: #58a6ff; }

        .service-name .link-arrow {
            font-size: 0.7rem;
            opacity: 0.4;
            margin-left: 0.2rem;
            transition: opacity 0.2s;
        }

        .service-name a:hover .link-arrow { opacity: 1; }

        .service-backend {
            display: flex;
            align-items: center;
            gap: 0.3rem;
            font-size: 0.78rem;
            margin-top: 0.1rem;
        }

        .backend-label {
            color: #6e7681;
            font-weight: 500;
            flex-shrink: 0;
        }

        .backend-link {
            color: #58a6ff;
            text-decoration: none;
            word-break: break-all;
            transition: color 0.2s;
        }

        .backend-link:hover {
            color: #79c0ff;
            text-decoration: underline;
        }

        .service-meta {
            display: flex;
            flex-direction: column;
            align-items: flex-end;
            gap: 0.3rem;
            flex-shrink: 0;
        }

        .response-time { font-size: 0.85rem; font-weight: 600; color: #8b949e; }
        .response-time.fast    { color: #3fb950; }
        .response-time.medium  { color: #d29922; }
        .response-time.slow    { color: #db6d28; }
        .response-time.timeout { color: #f85149; }

        .uptime-bar {
            width: 80px; height: 6px;
            background: #21262d;
            border-radius: 3px;
            overflow: hidden;
        }

        .uptime-fill {
            height: 100%;
            border-radius: 3px;
            transition: width 0.3s;
        }

        .uptime-text { font-size: 0.7rem; color: #8b949e; }

        .service-actions { display: flex; gap: 0.4rem; flex-shrink: 0; }

        .btn-icon {
            width: 36px; height: 36px;
            display: flex; align-items: center; justify-content: center;
            border: 1px solid #30363d;
            border-radius: 8px;
            background: #21262d;
            color: #c9d1d9;
            cursor: pointer;
            font-size: 1rem;
            transition: all 0.2s;
        }

        .btn-icon:hover        { background: #30363d; border-color: #58a6ff; }
        .btn-icon.delete:hover { border-color: #f85149; color: #f85149; }

        .btn-icon.spinning { animation: spin 1s linear infinite; }
        @keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }

        /* === Add Service Form === */
        .add-service {
            background: #161b22;
            border: 1px solid #30363d;
            border-radius: 10px;
            padding: 1.25rem;
        }

        .add-service h3 { color: #f0f6fc; margin-bottom: 1rem; font-size: 1rem; }

        .form-row { display: flex; gap: 0.75rem; flex-wrap: wrap; }

        .form-row input {
            flex: 1; min-width: 150px;
            padding: 0.5rem 0.75rem;
            background: #0d1117;
            border: 1px solid #30363d;
            border-radius: 6px;
            color: #c9d1d9;
            font-size: 0.9rem;
        }

        .form-row input:focus       { outline: none; border-color: #58a6ff; }
        .form-row input::placeholder { color: #484f58; }

        .add-note {
            margin-top: 0.6rem;
            font-size: 0.75rem;
            color: #6e7681;
            font-style: italic;
        }

        /* === Footer === */
        footer {
            text-align: center;
            padding-top: 2rem;
            color: #484f58;
            font-size: 0.8rem;
        }

        footer a {
            color: #58a6ff;
            text-decoration: none;
        }

        footer a:hover { text-decoration: underline; }

        .loading { text-align: center; padding: 2rem; color: #8b949e; }

        /* === Responsive === */
        @media (max-width: 600px) {
            body { padding: 1rem; }
            header h1 { font-size: 1.6rem; }
            .logo-icon { width: 80px; height: 80px; }
            .service-card { flex-wrap: wrap; }
            .service-meta { flex-direction: row; align-items: center; width: 100%; }
            .service-actions { width: 100%; justify-content: flex-end; }
            .status-legend { font-size: 0.72rem; gap: 0.5rem 0.75rem; }
        }
    </style>
</head>
<body>
    <div class="container">
        <!-- Header — Logo above text -->
        <header>
            <svg class="logo-icon" viewBox="220 270 580 460" xmlns="http://www.w3.org/2000/svg">
                <g id="layer1">
                    <path fill="#00a6fe" d="M 291.29 270.28 C 314.84 258.89 338.40 247.49 361.99 236.19 C 399.13 254.75 436.31 273.21 473.65 291.37 C 480.63 295.01 487.67 298.51 494.63 302.19 C 486.50 323.39 479.07 344.86 471.05 366.11 C 462.37 390.01 453.20 413.72 444.63 437.66 C 437.29 437.33 429.89 437.95 422.60 437.33 C 399.27 420.67 376.07 403.78 352.43 387.56 C 324.66 367.98 296.79 348.54 269.02 328.96 C 255.58 319.90 242.60 310.17 229.09 301.21 C 249.61 290.48 270.58 280.64 291.29 270.28 Z"/>
                    <path fill="#00a6fe" d="M 658.95 237.93 C 662.07 235.76 665.39 238.50 668.35 239.60 C 707.60 258.21 746.43 277.69 785.61 296.45 C 788.78 297.94 791.98 299.36 795.03 301.12 C 788.17 306.29 780.81 310.76 773.90 315.86 C 756.57 327.82 739.41 340.03 722.12 352.04 C 709.17 360.71 696.63 369.98 683.73 378.70 C 656.85 397.44 630.12 416.40 603.51 435.52 C 600.95 437.28 598.56 439.50 595.55 440.44 C 590.51 440.88 585.43 440.42 580.38 440.64 C 576.55 429.85 572.36 419.20 568.73 408.34 C 561.03 387.39 553.26 366.48 545.57 345.53 C 540.38 330.93 535.12 316.36 529.56 301.91 C 532.05 300.12 534.89 298.95 537.63 297.62 C 562.92 284.74 588.60 272.65 614.06 260.11 C 628.97 252.61 644.11 245.57 658.95 237.93 Z"/>
                </g>
                <g id="layer2">
                    <path fill="#0086f2" d="M 796.44 316.46 C 799.22 314.73 801.61 312.35 804.69 311.12 C 810.33 335.05 815.44 359.11 820.87 383.09 C 824.29 398.05 827.25 413.11 830.87 428.02 C 832.86 438.03 834.80 448.09 837.70 457.89 C 833.46 457.63 829.23 457.62 825.00 457.65 C 772.67 457.66 720.33 457.64 668.00 457.67 C 665.17 457.61 662.30 457.97 659.52 457.39 C 656.04 455.33 653.35 452.21 650.10 449.85 C 645.30 446.34 640.71 442.57 636.10 438.84 C 633.41 436.63 630.42 434.78 628.05 432.21 C 640.51 422.89 653.20 413.85 666.01 405.02 C 697.70 382.57 729.79 360.69 762.06 339.09 C 773.70 331.82 784.92 323.90 796.44 316.46 Z"/>
                    <path fill="#0086f2" d="M 218.51 316.58 C 218.82 314.93 219.18 313.26 219.96 311.76 C 241.46 326.41 263.34 340.51 284.85 355.15 C 307.33 370.29 329.88 385.35 352.06 400.93 C 366.79 411.22 381.43 421.65 396.05 432.09 C 394.92 433.27 393.78 434.46 392.50 435.49 C 384.50 441.81 376.39 448.00 368.44 454.40 C 366.92 455.55 365.41 456.82 363.59 457.49 C 359.75 457.98 355.86 457.56 351.99 457.65 C 297.40 457.73 242.81 457.52 188.22 457.75 C 189.73 452.16 190.55 446.42 191.88 440.79 C 196.74 417.37 201.75 393.98 206.77 370.59 C 207.13 368.88 207.96 367.33 208.39 365.64 C 210.82 349.11 215.29 332.97 218.51 316.58 Z"/>
                </g>
                <g id="pulse-line">
                    <path fill="#25efff" d="M 512.33 325.10 C 512.65 325.20 513.28 325.40 513.59 325.51 C 520.49 352.92 527.41 380.35 534.19 407.80 C 540.73 435.78 548.05 463.57 554.41 491.60 C 556.68 499.98 558.49 508.47 560.31 516.96 C 561.88 514.43 562.66 511.54 563.86 508.84 C 569.31 496.53 574.45 484.09 579.80 471.73 C 580.45 470.46 580.95 468.94 582.22 468.13 C 586.21 467.80 590.21 468.36 594.20 468.61 C 617.71 470.11 641.23 471.69 664.78 472.43 C 675.13 473.48 685.56 472.88 695.93 473.77 C 705.47 474.40 715.03 474.11 724.58 474.50 C 735.39 475.24 746.23 475.26 757.06 475.35 C 772.04 475.89 787.02 476.24 802.01 476.35 C 810.38 476.26 818.74 476.56 827.09 476.87 C 825.52 479.31 823.85 481.77 821.51 483.54 C 820.12 484.18 818.52 483.99 817.05 484.07 C 800.03 483.88 783.02 484.83 766.01 484.67 C 740.67 484.82 715.33 485.24 690.00 485.67 C 659.44 485.56 628.87 486.19 598.31 486.28 C 597.12 487.19 596.66 488.71 595.96 489.98 C 591.84 498.80 587.79 507.65 583.48 516.38 C 575.73 533.20 567.75 549.92 559.42 566.47 C 556.87 571.12 554.75 576.00 551.86 580.45 C 551.36 579.46 550.94 578.45 550.60 577.42 C 547.33 565.48 544.40 553.45 541.59 541.40 C 537.80 527.29 534.76 512.99 531.18 498.82 C 524.60 470.89 517.48 443.06 512.13 414.86 C 511.42 411.11 511.15 407.28 509.95 403.64 C 509.33 404.89 508.78 406.17 508.50 407.54 C 501.76 434.67 493.74 461.45 486.41 488.42 C 482.85 500.45 479.84 512.66 476.06 524.63 C 471.29 519.09 467.50 512.79 463.14 506.94 C 458.05 499.38 452.43 492.20 447.43 484.58 C 446.44 483.10 445.26 481.77 444.07 480.47 C 440.75 482.74 439.02 486.69 435.87 488.99 C 411.56 488.41 387.25 487.68 362.93 487.67 C 345.00 486.92 327.04 486.74 309.10 486.58 C 292.42 485.49 275.71 485.77 259.01 485.58 C 240.50 484.49 221.94 484.78 203.41 484.57 C 201.18 482.26 198.52 480.25 197.02 477.34 C 212.65 477.40 228.29 477.22 243.92 476.68 C 249.93 476.22 255.96 476.39 261.99 476.35 C 286.66 476.35 311.32 475.88 335.98 475.34 C 354.00 475.34 372.02 475.18 390.03 475.07 C 392.84 475.05 395.63 475.63 398.44 475.35 C 406.87 474.36 415.38 475.12 423.84 474.76 C 425.13 474.78 425.98 473.63 426.90 472.89 C 430.45 469.44 433.44 465.46 436.87 461.90 C 441.19 457.38 444.85 452.21 449.61 448.12 C 452.64 455.61 456.64 462.66 460.03 469.99 C 462.06 473.65 463.31 477.76 465.88 481.11 C 469.37 473.03 471.50 464.42 474.03 456.00 C 486.20 416.18 497.14 376.00 509.17 336.13 C 510.33 332.49 511.44 328.82 512.33 325.10 Z"/>
                </g>
                <g id="layer3">
                    <path fill="#0062dd" d="M 773.07 501.83 C 784.13 501.17 795.22 500.91 806.30 500.45 C 805.88 501.27 805.40 502.05 804.88 502.79 C 789.35 519.95 773.38 536.70 757.69 553.69 C 748.03 564.36 738.13 574.81 728.29 585.32 C 721.47 593.15 713.85 600.23 706.92 607.95 C 702.99 612.34 698.73 616.41 694.64 620.65 C 692.58 622.75 690.67 625.04 688.25 626.77 C 672.78 611.22 656.40 596.60 640.72 581.26 C 631.56 573.12 623.08 564.25 613.93 556.10 C 610.05 552.65 606.46 548.88 602.53 545.48 C 601.45 544.59 600.57 543.50 599.90 542.29 C 612.41 531.02 624.31 519.09 636.77 507.77 C 639.53 506.27 642.99 507.10 646.04 506.66 C 655.68 505.71 665.40 506.12 675.06 505.33 C 679.36 504.97 683.68 504.98 688.01 504.97 C 693.34 504.98 698.64 504.17 703.97 504.07 C 727.02 503.89 750.03 502.37 773.07 501.83 Z"/>
                    <path fill="#0062dd" d="M 216.77 501.11 C 231.53 500.99 246.27 502.00 261.02 502.35 C 272.70 503.03 284.41 502.93 296.09 503.58 C 305.48 504.06 314.88 503.83 324.25 504.54 C 332.63 505.10 341.05 504.77 349.42 505.50 C 357.74 506.16 366.10 505.66 374.41 506.51 C 378.51 506.95 382.66 506.64 386.74 507.23 C 390.27 509.24 392.86 512.51 395.89 515.16 C 404.87 523.15 413.42 531.62 422.59 539.39 C 423.37 540.14 425.26 541.44 423.79 542.55 C 422.19 544.36 420.26 545.82 418.46 547.42 C 411.75 553.37 405.57 559.87 399.01 565.99 C 393.32 570.96 387.99 576.32 382.52 581.54 C 375.46 587.79 368.81 594.48 362.03 601.03 C 356.02 606.34 350.33 612.00 344.57 617.58 C 341.49 620.51 338.80 623.93 335.07 626.10 C 326.41 618.05 318.40 609.33 310.28 600.74 C 304.95 595.07 299.29 589.73 294.05 583.98 C 287.28 576.10 279.47 569.20 272.74 561.29 C 262.37 550.31 252.06 539.27 241.81 528.18 C 236.15 522.50 231.09 516.25 225.35 510.64 C 222.50 507.45 219.12 504.71 216.77 501.11 Z"/>
                </g>
                <g id="layer4">
                    <path fill="#0041ba" d="M 428.71 555.73 C 430.79 553.81 432.65 551.59 435.08 550.10 C 437.56 550.97 439.31 553.11 441.32 554.72 C 456.17 567.49 470.61 580.74 485.60 593.36 C 491.85 599.16 498.57 604.44 504.96 610.11 C 506.05 611.26 507.69 612.33 507.71 614.11 C 508.30 644.72 507.63 675.35 508.65 705.95 C 508.60 726.94 508.83 747.94 509.01 768.94 C 493.87 755.70 478.08 743.23 462.74 730.23 C 438.37 710.30 414.43 689.85 389.98 670.02 C 375.17 657.29 359.75 645.26 345.17 632.27 C 357.29 621.01 369.64 610.02 381.76 598.76 C 397.29 584.28 413.38 570.42 428.71 555.73 Z"/>
                    <path fill="#0041ba" d="M 584.29 562.26 C 587.15 559.59 590.22 557.14 593.12 554.50 C 595.35 555.62 597.11 557.43 598.95 559.09 C 612.43 570.90 625.61 583.06 639.13 594.83 C 644.26 599.74 649.61 604.41 654.91 609.14 C 663.24 616.24 671.07 623.90 679.28 631.14 C 678.19 633.05 676.38 634.36 674.71 635.73 C 637.26 667.29 599.40 698.38 561.85 729.82 C 547.46 741.78 532.86 753.51 518.62 765.65 C 517.88 766.20 517.10 766.67 516.26 767.06 C 516.96 756.52 517.17 745.96 517.49 735.41 C 518.87 720.98 517.71 706.45 519.00 692.02 C 519.12 674.98 520.00 657.97 520.22 640.93 C 520.69 634.28 520.72 627.61 520.84 620.95 C 520.54 618.42 523.07 617.17 524.60 615.64 C 544.39 597.73 564.70 580.40 584.29 562.26 Z"/>
                </g>
            </svg>
            <h1>Shell<span class="brand-accent">ty</span> Pulse</h1>
            <p class="subtitle">Service Health Monitor</p>
        </header>

        <!-- Overall Status Banner -->
        <div id="overall-status" class="overall-status unknown">Loading...</div>

        <!-- Status Legend -->
        <div class="status-legend">
            <span class="legend-item"><span class="legend-dot operational"></span> Operational <span class="legend-desc">(&lt; 1s)</span></span>
            <span class="legend-item"><span class="legend-dot degraded"></span> Degraded <span class="legend-desc">(1-3s)</span></span>
            <span class="legend-item"><span class="legend-dot slow"></span> Slow <span class="legend-desc">(&gt; 3s)</span></span>
            <span class="legend-item"><span class="legend-dot down"></span> Down <span class="legend-desc">(error/timeout)</span></span>
            <span class="legend-item"><span class="legend-dot unknown"></span> Unknown <span class="legend-desc">(not checked)</span></span>
        </div>

        <!-- Control Buttons -->
        <div class="controls">
            <button class="btn primary" onclick="checkAll(this)">⟳ Check All Now</button>
            <button id="auto-ping-btn" class="btn active" onclick="toggleAutoPing()">
                ⏱ Auto-Ping: ON
            </button>
            <div class="interval-selector">
                <span class="interval-label">Interval:</span>
                <button class="interval-btn" data-interval="600" onclick="setPingInterval(600)">10 min</button>
                <button class="interval-btn" data-interval="900" onclick="setPingInterval(900)">15 min</button>
                <button class="interval-btn" data-interval="1800" onclick="setPingInterval(1800)">30 min</button>
                <button class="interval-btn" data-interval="3600" onclick="setPingInterval(3600)">1 hour</button>
            </div>
            <div id="check-info" class="check-info">Loading...</div>
        </div>

        <!-- Services List -->
        <div id="services-grid" class="services-grid">
            <div class="loading">Loading services...</div>
        </div>

        <!-- Add Service Form -->
        <div class="add-service">
            <h3>➕ Add New Service</h3>
            <div class="form-row">
                <input type="text" id="new-name" placeholder="Service name" maxlength="100" />
                <input type="text" id="new-url" placeholder="Health check URL (https://...)" maxlength="2048" />
                <input type="text" id="new-frontend-url" placeholder="Frontend URL — optional" maxlength="2048" />
                <button class="btn primary" onclick="addService()">Add</button>
            </div>
            <p class="add-note">⚠ Services added here are stored in memory and will be reset on application restart.</p>
        </div>

        <footer>
            Service Health Monitor by
            <a href="https://shellty-it.github.io" target="_blank" rel="noopener noreferrer">Shellty IT</a>
        </footer>
    </div>

    <script>
        /* ========================================
         * Constants & Status Helpers
         * ======================================== */
        var REFRESH_INTERVAL = 15000;

        var STATUS_CONFIG = {
            operational: { icon: '🟢', label: 'All Systems Operational' },
            degraded:    { icon: '🟡', label: 'Performance Degraded' },
            slow:        { icon: '🟠', label: 'Slow Response Detected' },
            down:        { icon: '🔴', label: 'Service Outage Detected' },
            unknown:     { icon: '⚪', label: 'Status Unknown' }
        };

        function getStatusConfig(status) {
            return STATUS_CONFIG[status] || STATUS_CONFIG.unknown;
        }

        function rtClass(ms) {
            if (ms === null || ms === undefined) return 'timeout';
            if (ms < 1000) return 'fast';
            if (ms <= 3000) return 'medium';
            return 'slow';
        }

        function rtText(ms) {
            if (ms === null || ms === undefined) return '—';
            return ms < 1000 ? ms + 'ms' : (ms / 1000).toFixed(2) + 's';
        }

        function uptimeColor(pct) {
            if (pct >= 95) return '#3fb950';
            if (pct >= 80) return '#d29922';
            if (pct >= 50) return '#db6d28';
            return '#f85149';
        }

        function timeAgo(iso) {
            if (!iso) return 'never';
            var diff = (Date.now() - new Date(iso).getTime()) / 1000;
            if (diff < 60) return Math.round(diff) + 's ago';
            if (diff < 3600) return Math.round(diff / 60) + ' min ago';
            return Math.round(diff / 3600) + 'h ago';
        }

        function escapeHtml(text) {
            var d = document.createElement('div');
            d.textContent = text;
            return d.innerHTML;
        }

        function intervalLabel(seconds) {
            if (seconds >= 3600) return Math.round(seconds / 3600) + 'h';
            return Math.round(seconds / 60) + ' min';
        }

        /* ========================================
         * API Communication (fetch, no reload)
         * ======================================== */
        async function fetchServices() {
            try {
                var res = await fetch('/api/services');
                var data = await res.json();
                renderDashboard(data);
            } catch (err) {
                console.error('Failed to fetch services:', err);
            }
        }

        async function checkService(id) {
            var btn = document.getElementById('check-btn-' + id);
            if (btn) btn.classList.add('spinning');
            try {
                await fetch('/api/services/' + id + '/check', { method: 'POST' });
                await fetchServices();
            } catch (err) {
                console.error('Check failed:', err);
            }
            if (btn) btn.classList.remove('spinning');
        }

        async function checkAll(btn) {
            btn.disabled = true;
            btn.textContent = '⟳ Checking...';
            try {
                await fetch('/api/check-all', { method: 'POST' });
                await fetchServices();
            } catch (err) {
                console.error('Check all failed:', err);
            }
            btn.disabled = false;
            btn.textContent = '⟳ Check All Now';
        }

        async function toggleAutoPing() {
            try {
                await fetch('/api/toggle-auto-ping', { method: 'POST' });
                await fetchServices();
            } catch (err) {
                console.error('Toggle failed:', err);
            }
        }

        async function setPingInterval(seconds) {
            try {
                var res = await fetch('/api/ping-interval', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ interval: seconds })
                });
                if (res.ok) {
                    await fetchServices();
                } else {
                    var errData = await res.json();
                    alert(errData.error || 'Failed to set interval.');
                }
            } catch (err) {
                console.error('Set interval failed:', err);
            }
        }

        async function addService() {
            var nameVal = document.getElementById('new-name').value.trim();
            var urlVal  = document.getElementById('new-url').value.trim();
            var frontendVal = document.getElementById('new-frontend-url').value.trim();
            if (!nameVal || !urlVal) { alert('Enter both name and health check URL.'); return; }

            var payload = { name: nameVal, url: urlVal };
            if (frontendVal) { payload.frontend_url = frontendVal; }

            try {
                var res = await fetch('/api/services', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                if (res.ok) {
                    document.getElementById('new-name').value = '';
                    document.getElementById('new-url').value = '';
                    document.getElementById('new-frontend-url').value = '';
                    await fetchServices();
                } else {
                    var errData = await res.json();
                    alert(errData.error || 'Failed to add service.');
                }
            } catch (err) {
                console.error('Add failed:', err);
            }
        }

        async function deleteService(id) {
            if (!confirm('Delete this service?')) return;
            try {
                await fetch('/api/services/' + id, { method: 'DELETE' });
                await fetchServices();
            } catch (err) {
                console.error('Delete failed:', err);
            }
        }

        /* ========================================
         * Render Dashboard (DOM update)
         * ======================================== */
        function renderDashboard(data) {
            var services = data.services;
            var meta     = data.meta;

            /* Overall status banner */
            var oel = document.getElementById('overall-status');
            var ocfg = getStatusConfig(meta.overall_status);
            oel.className = 'overall-status ' + meta.overall_status;
            oel.textContent = ocfg.icon + ' ' + ocfg.label;

            /* Auto-ping button */
            var apb = document.getElementById('auto-ping-btn');
            if (meta.auto_ping_enabled) {
                apb.className = 'btn active';
                apb.textContent = '⏱ Auto-Ping: ON';
            } else {
                apb.className = 'btn inactive';
                apb.textContent = '⏱ Auto-Ping: OFF';
            }

            /* Interval buttons — highlight active, disable when auto-ping off */
            var intervalBtns = document.querySelectorAll('.interval-btn');
            intervalBtns.forEach(function(btn) {
                var btnInterval = parseInt(btn.getAttribute('data-interval'));
                btn.classList.toggle('active', btnInterval === meta.ping_interval);
                btn.disabled = !meta.auto_ping_enabled;
            });

            /* Check info line */
            var ci = document.getElementById('check-info');
            var intLabel = intervalLabel(meta.ping_interval);
            if (meta.last_check) {
                var ago = timeAgo(meta.last_check);
                if (meta.auto_ping_enabled) {
                    var elapsed = (Date.now() - new Date(meta.last_check).getTime()) / 1000;
                    var remain  = Math.max(0, Math.round((meta.ping_interval - elapsed) / 60));
                    ci.textContent = 'Last check: ' + ago + ' · Next in: ~' + remain + ' min · Auto-ping every ' + intLabel;
                } else {
                    ci.textContent = 'Last check: ' + ago + ' · Auto-ping disabled';
                }
            } else {
                ci.textContent = 'No checks yet · Auto-ping every ' + intLabel;
            }

            /* Services grid */
            var grid = document.getElementById('services-grid');
            if (services.length === 0) {
                grid.innerHTML = '<div class="loading">No services configured. Add one below.</div>';
                return;
            }

            grid.innerHTML = services.map(function(svc) {
                var cfg = getStatusConfig(svc.status);
                var upPct   = svc.uptime_percent !== null ? svc.uptime_percent.toFixed(1) : '—';
                var upColor = svc.uptime_percent !== null ? uptimeColor(svc.uptime_percent) : '#484f58';
                var upWidth = svc.uptime_percent !== null ? svc.uptime_percent : 0;

                /* Service name — linked to frontend if frontend_url exists */
                var nameHtml;
                if (svc.frontend_url) {
                    nameHtml = '<a href="' + escapeHtml(svc.frontend_url) + '" target="_blank" rel="noopener noreferrer">' +
                               escapeHtml(svc.name) +
                               ' <span class="link-arrow">↗</span></a>';
                } else {
                    nameHtml = escapeHtml(svc.name);
                }

                /* Backend health URL — always shown as clickable link */
                var backendHtml = '<span class="backend-label">Backend:</span> ' +
                    '<a href="' + escapeHtml(svc.url) + '" target="_blank" rel="noopener noreferrer" class="backend-link">' +
                    escapeHtml(svc.url) + '</a>';

                return '<div class="service-card">' +
                    '<div class="status-icon">' + cfg.icon + '</div>' +
                    '<div class="service-info">' +
                        '<div class="service-name">' + nameHtml + '</div>' +
                        '<div class="service-backend">' + backendHtml + '</div>' +
                    '</div>' +
                    '<div class="service-meta">' +
                        '<div class="response-time ' + rtClass(svc.response_time_ms) + '">' + rtText(svc.response_time_ms) + '</div>' +
                        '<div class="uptime-bar"><div class="uptime-fill" style="width:' + upWidth + '%;background:' + upColor + '"></div></div>' +
                        '<div class="uptime-text">Uptime: ' + upPct + '%</div>' +
                    '</div>' +
                    '<div class="service-actions">' +
                        '<button id="check-btn-' + svc.id + '" class="btn-icon" onclick="checkService(\\'' + svc.id + '\\')" title="Check now">⟳</button>' +
                        '<button class="btn-icon delete" onclick="deleteService(\\'' + svc.id + '\\')" title="Delete">✕</button>' +
                    '</div>' +
                '</div>';
            }).join('');
        }

        /* ========================================
         * Initialization — fetch + auto-refresh
         * ======================================== */
        fetchServices();
        setInterval(fetchServices, REFRESH_INTERVAL);
    </script>
</body>
</html>"""


# ============================================
# Flask Application
# ============================================
app = Flask(__name__)


# ============================================
# Helper Functions
# ============================================

def generate_id():
    """Generate a short unique ID for a service."""
    return uuid.uuid4().hex[:8]


def determine_status(response_time_seconds, success):
    """
    Determine service status based on response time and HTTP success.

    Rules:
        HTTP 200 + < 1s   → operational
        HTTP 200 + 1-3s   → degraded
        HTTP 200 + > 3s   → slow
        HTTP error/timeout → down

    Args:
        response_time_seconds: float, elapsed time in seconds
        success: bool, True if HTTP 200

    Returns:
        str: status string
    """
    if not success:
        return "down"
    if response_time_seconds < 1.0:
        return "operational"
    if response_time_seconds <= 3.0:
        return "degraded"
    return "slow"


def get_overall_status():
    """
    Determine overall status — worst status among all services.

    Priority (highest = worst):
        down > slow > degraded > operational > unknown
    """
    priority = {
        "unknown": 0,
        "operational": 1,
        "degraded": 2,
        "slow": 3,
        "down": 4,
    }

    with services_lock:
        if not services:
            return "unknown"

        worst = "unknown"
        for svc in services:
            status = svc.get("status", "unknown")
            if priority.get(status, 0) > priority.get(worst, 0):
                worst = status
        return worst


def create_service(name, url, frontend_url=None):
    """
    Create a new service record with default values.

    Args:
        name:         display name
        url:          full health check URL (backend)
        frontend_url: optional URL to the frontend application

    Returns:
        dict: service record
    """
    return {
        "id": generate_id(),
        "name": name,
        "url": url,
        "frontend_url": frontend_url,
        "status": "unknown",
        "response_time_ms": None,
        "last_check": None,
        "total_checks": 0,
        "successful_checks": 0,
        "uptime_percent": None,
    }


def check_single_service(service):
    """
    Perform HTTP GET health check on a single service.

    Measures response time, determines status, updates service record in-place.

    Args:
        service: dict — service record (modified in-place)
    """
    url = service["url"]
    name = service["name"]
    logger.info("Checking service: %s (%s)", name, url)

    try:
        start = time.time()
        response = http_requests.get(url, timeout=REQUEST_TIMEOUT)
        elapsed = time.time() - start

        success = response.status_code == 200
        status = determine_status(elapsed, success)
        response_time_ms = round(elapsed * 1000)

        if success:
            logger.info("  ✓ %s → %s (HTTP %d, %dms)", name, status, response.status_code, response_time_ms)
        else:
            logger.warning("  ✗ %s → down (HTTP %d, %dms)", name, response.status_code, response_time_ms)

    except http_requests.exceptions.Timeout:
        logger.error("  ✗ %s → down (timeout after %ds)", name, REQUEST_TIMEOUT)
        status = "down"
        response_time_ms = None
        success = False

    except http_requests.exceptions.RequestException as exc:
        logger.error("  ✗ %s → down (error: %s)", name, str(exc))
        status = "down"
        response_time_ms = None
        success = False

    # Thread-safe update of service record
    with services_lock:
        service["status"] = status
        service["response_time_ms"] = response_time_ms
        service["last_check"] = datetime.now(timezone.utc).isoformat()
        service["total_checks"] += 1
        if success:
            service["successful_checks"] += 1
        # Recalculate uptime percentage
        if service["total_checks"] > 0:
            service["uptime_percent"] = round(
                (service["successful_checks"] / service["total_checks"]) * 100, 2
            )


def check_all_services():
    """
    Run health check on all registered services.

    Called by scheduler (auto-ping) or manually via API.
    Updates global last_check_time.
    """
    global last_check_time

    logger.info("=" * 50)
    logger.info("Starting health check for all services (%d total)", len(services))

    # Snapshot list (shallow — same dict objects, so updates apply)
    with services_lock:
        snapshot = list(services)

    for svc in snapshot:
        check_single_service(svc)

    last_check_time = datetime.now(timezone.utc).isoformat()
    logger.info("Health check complete.")
    logger.info("=" * 50)


def scheduled_check():
    """Scheduler wrapper — respects auto_ping_enabled flag."""
    if auto_ping_enabled:
        check_all_services()
    else:
        logger.info("Auto-ping disabled — skipping scheduled check.")


def load_services_from_env():
    """
    Parse SERVICES environment variable and preload services.

    Expected format: JSON array of objects with required "name" and "url",
    and optional "frontend_url" fields.

    Example:
        [{"name": "App", "url": "https://api.example.com/health", "frontend_url": "https://example.com"}]

    Silently skips invalid entries.
    """
    try:
        parsed = json.loads(SERVICES_JSON)
        if not isinstance(parsed, list):
            logger.error("SERVICES env var is not a JSON array — ignoring.")
            return

        for item in parsed:
            if isinstance(item, dict) and "name" in item and "url" in item:
                svc = create_service(
                    name=item["name"],
                    url=item["url"],
                    frontend_url=item.get("frontend_url"),
                )
                services.append(svc)
                logger.info("  Preloaded: %s → %s", item["name"], item["url"])
                if item.get("frontend_url"):
                    logger.info("    Frontend: %s", item["frontend_url"])
            else:
                logger.warning("  Skipping invalid entry: %s", item)

        logger.info("Loaded %d services from environment.", len(services))

    except json.JSONDecodeError as exc:
        logger.error("Failed to parse SERVICES env var: %s", str(exc))


# ============================================
# Flask Routes
# ============================================

@app.route("/")
def dashboard():
    """Serve the main dashboard HTML page."""
    return render_template_string(DASHBOARD_HTML)


@app.route("/health")
def health():
    """
    Self health check endpoint.

    Always returns HTTP 200 with JSON status.
    Used by Docker HEALTHCHECK and external monitors.
    """
    return jsonify({
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "service": "shellty-pulse",
    }), 200


@app.route("/api/services", methods=["GET"])
def get_services():
    """
    List all monitored services with their current statuses.

    Returns JSON with services array and meta information
    (overall status, auto-ping state, timing info).
    """
    with services_lock:
        services_data = [svc.copy() for svc in services]

    return jsonify({
        "services": services_data,
        "meta": {
            "overall_status": get_overall_status(),
            "auto_ping_enabled": auto_ping_enabled,
            "ping_interval": ping_interval,
            "last_check": last_check_time,
            "total_services": len(services_data),
        },
    })


@app.route("/api/services", methods=["POST"])
def add_service():
    """
    Add a new service to monitor.

    Expects JSON body:
        {
            "name": "Service Name",                          (required)
            "url": "https://example.com/health",             (required)
            "frontend_url": "https://example.com"            (optional)
        }

    Returns created service with HTTP 201.
    """
    data = request.get_json(silent=True)

    if not data:
        return jsonify({"error": "Request body must be valid JSON."}), 400

    name = data.get("name", "").strip()
    url = data.get("url", "").strip()
    frontend_url = data.get("frontend_url", "").strip() or None

    if not name or not url:
        return jsonify({"error": "Both 'name' and 'url' are required."}), 400

    if len(name) > MAX_NAME_LENGTH:
        return jsonify({"error": f"Name must be at most {MAX_NAME_LENGTH} characters."}), 400

    if len(url) > MAX_URL_LENGTH:
        return jsonify({"error": f"URL must be at most {MAX_URL_LENGTH} characters."}), 400

    if not url.startswith(("http://", "https://")):
        return jsonify({"error": "URL must start with http:// or https://"}), 400

    if frontend_url and not frontend_url.startswith(("http://", "https://")):
        return jsonify({"error": "Frontend URL must start with http:// or https://"}), 400

    if frontend_url and len(frontend_url) > MAX_URL_LENGTH:
        return jsonify({"error": f"Frontend URL must be at most {MAX_URL_LENGTH} characters."}), 400

    svc = create_service(name, url, frontend_url)

    with services_lock:
        services.append(svc)

    logger.info("Added service: %s → %s (id: %s)", name, url, svc["id"])
    return jsonify(svc), 201


@app.route("/api/services/<service_id>", methods=["DELETE"])
def delete_service(service_id):
    """
    Remove a service by its ID.

    Returns HTTP 204 on success, 404 if not found.
    """
    with services_lock:
        for i, svc in enumerate(services):
            if svc["id"] == service_id:
                removed = services.pop(i)
                logger.info("Deleted service: %s (id: %s)", removed["name"], service_id)
                return "", 204

    return jsonify({"error": "Service not found."}), 404


@app.route("/api/services/<service_id>/check", methods=["POST"])
def check_service_endpoint(service_id):
    """
    Manually trigger health check for a single service.

    Returns updated service data.
    """
    target = None
    with services_lock:
        for svc in services:
            if svc["id"] == service_id:
                target = svc
                break

    if not target:
        return jsonify({"error": "Service not found."}), 404

    check_single_service(target)

    with services_lock:
        return jsonify(target.copy())


@app.route("/api/check-all", methods=["POST"])
def check_all_endpoint():
    """
    Manually trigger health check for all services.

    Returns updated services list with overall status.
    """
    check_all_services()

    with services_lock:
        services_data = [svc.copy() for svc in services]

    return jsonify({
        "message": "All services checked.",
        "services": services_data,
        "overall_status": get_overall_status(),
    })


@app.route("/api/toggle-auto-ping", methods=["POST"])
def toggle_auto_ping():
    """
    Toggle automatic periodic health checking on/off.

    Returns current auto-ping state.
    """
    global auto_ping_enabled
    auto_ping_enabled = not auto_ping_enabled
    state = "enabled" if auto_ping_enabled else "disabled"
    logger.info("Auto-ping toggled: %s", state)

    return jsonify({
        "auto_ping_enabled": auto_ping_enabled,
        "message": f"Auto-ping {state}.",
    })


@app.route("/api/ping-interval", methods=["POST"])
def set_ping_interval():
    """
    Change the auto-ping interval.

    Accepts JSON: {"interval": 600}
    Valid values: 600 (10 min), 900 (15 min), 1800 (30 min), 3600 (1 hour).
    Reschedules the background job immediately.
    """
    global ping_interval

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be valid JSON."}), 400

    new_interval = data.get("interval")
    if new_interval not in AVAILABLE_INTERVALS:
        valid = [f"{v} ({k}s)" for k, v in AVAILABLE_INTERVALS.items()]
        return jsonify({
            "error": f"Invalid interval. Valid options: {', '.join(valid)}"
        }), 400

    ping_interval = new_interval

    # Reschedule background job with new interval
    if scheduler:
        scheduler.reschedule_job(
            "health_check_job",
            trigger="interval",
            seconds=ping_interval,
        )

    label = AVAILABLE_INTERVALS[ping_interval]
    logger.info("Ping interval changed to %s (%ds)", label, ping_interval)

    return jsonify({
        "interval": ping_interval,
        "label": label,
        "message": f"Auto-ping interval set to {label}.",
    })


# ============================================
# Application Initialization (module-level)
# ============================================
# Runs on import (gunicorn) and direct execution (python app.py).
# Ensures scheduler and services work in both scenarios.

logger.info("=" * 50)
logger.info("  Starting Shellty Pulse — Service Health Monitor")
logger.info("=" * 50)
logger.info("Configuration:")
logger.info("  PING_INTERVAL:   %d seconds (%d min)", ping_interval, ping_interval // 60)
logger.info("  REQUEST_TIMEOUT: %d seconds", REQUEST_TIMEOUT)

load_services_from_env()

scheduler = BackgroundScheduler(daemon=True)
scheduler.add_job(
    func=scheduled_check,
    trigger="interval",
    seconds=ping_interval,
    id="health_check_job",
    name="Periodic Health Check",
    replace_existing=True,
)
scheduler.start()
logger.info("Scheduler started — checking every %d seconds.", ping_interval)

threading.Thread(target=check_all_services, daemon=True).start()
logger.info("Initial health check started in background.")
logger.info("=" * 50)


# ============================================
# Direct Execution (development only)
# ============================================
if __name__ == "__main__":
    logger.info("Development mode — Dashboard: http://0.0.0.0:5000")
    app.run(host="0.0.0.0", port=5000, debug=False)