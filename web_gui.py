# Save as web_gui.py
from flask import Flask, render_template_string, request, jsonify, send_file
import threading
import asyncio
import csv
import sys
if sys.platform == 'win32':
    import msvcrt
    def _lock_shared(f):
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
    def _lock_exclusive(f):
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
    def _unlock(f):
        try:
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
else:
    import fcntl
    def _lock_shared(f):
        fcntl.flock(f.fileno(), fcntl.LOCK_SH)
    def _lock_exclusive(f):
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
    def _unlock(f):
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
import json
import time
import random
import io
from pathlib import Path
import logging
import os
import re
from datetime import datetime
from dotenv import load_dotenv
from odf.opendocument import load as load_ods
from odf.table import Table, TableRow, TableCell
from odf.text import P

app = Flask(__name__)
bot_thread = None
bot_running = False
stop_event = threading.Event()
current_bot = None

# Cooldown state
cooldown_active = False
cooldown_end = 0
skip_cooldown_flag = False
cooldown_enabled = True
work_interval = 15  # minutes
rest_min = 2  # minutes
rest_max = 3  # minutes

CSV_FIELDNAMES = ['item_number', 'quantity', 'name', 'size', 'units', 'order_filled']


def _read_csv_locked():
    """Read orders.csv with shared file lock."""
    orders = []
    if Path('orders.csv').exists():
        with open('orders.csv', 'r', newline='') as f:
            _lock_shared(f)
            try:
                orders = list(csv.DictReader(f))
            finally:
                _unlock(f)
    return orders


def _write_csv_locked(orders):
    """Write orders.csv with exclusive file lock."""
    with open('orders.csv', 'w', newline='') as f:
        _lock_exclusive(f)
        try:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES, extrasaction='ignore')
            writer.writeheader()
            writer.writerows(orders)
        finally:
            _unlock(f)

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# HTML template
HTML_TEMPLATE = '''
<!DOCTYPE html>
<html>
<head>
    <title>Mississippi DOR Order Bot</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 20px; background: #f0f0f0; }
        .container { max-width: 1200px; margin: 0 auto; background: white; padding: 20px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
        h1 { color: #333; border-bottom: 3px solid #007bff; padding-bottom: 10px; }
        .tabs { display: flex; gap: 10px; margin-bottom: 20px; }
        .tab { padding: 10px 20px; background: #f0f0f0; cursor: pointer; border-radius: 5px; transition: all 0.3s; }
        .tab:hover { background: #e0e0e0; }
        .tab.active { background: #007bff; color: white; }
        .tab-content { display: none; padding: 20px; background: #f9f9f9; border-radius: 5px; }
        .tab-content.active { display: block; }
        input, select { padding: 8px; margin: 5px; border: 1px solid #ddd; border-radius: 4px; }
        button { padding: 10px 20px; margin: 5px; background: #007bff; color: white; border: none; border-radius: 4px; cursor: pointer; }
        button:hover { background: #0056b3; }
        button.danger { background: #dc3545; }
        button.danger:hover { background: #c82333; }
        button.success { background: #28a745; }
        button.success:hover { background: #218838; }
        table { width: 100%; border-collapse: collapse; margin-top: 20px; }
        th { background: #007bff; color: white; }
        th, td { border: 1px solid #ddd; padding: 10px; text-align: left; }
        tr:nth-child(even) { background: #f9f9f9; }
        .status { font-size: 20px; font-weight: bold; margin: 20px 0; padding: 10px; border-radius: 5px; }
        .running { background: #d4edda; color: #155724; }
        .stopped { background: #f8d7da; color: #721c24; }
        #logs-content { background: #2c3e50; color: #ecf0f1; padding: 15px; height: 400px; overflow-y: scroll; font-family: monospace; font-size: 12px; border-radius: 5px; }
        .log-entry { margin: 2px 0; }
        .stats-box { background: #e9ecef; padding: 15px; border-radius: 5px; margin: 20px 0; }
        .input-group { margin: 10px 0; }
        .input-group label { display: inline-block; width: 120px; }
        .trace-skip { color: #999; }
        .trace-added { color: #28a745; font-weight: bold; }
        .trace-failed { color: #dc3545; font-weight: bold; }
        .cooldown { background: #fff3cd; color: #856404; }
        button:disabled { opacity: 0.5; cursor: not-allowed; }
    </style>
</head>
<body>
    <div class="container">
        <h1>🛒 Mississippi DOR Order Bot</h1>
        
        <div class="tabs">
            <div class="tab active" onclick="showTab('settings')">⚙️ Settings</div>
            <div class="tab" onclick="showTab('orders')">📋 Orders</div>
            <div class="tab" onclick="showTab('control')">🎮 Control</div>
            <div class="tab" onclick="showTab('logs')">📜 Logs</div>
            <div class="tab" onclick="showTab('orderdata')">📂 Order Data</div>
            <div class="tab" onclick="showTab('specialorders')">⭐ Special Orders</div>
            <div class="tab" onclick="showTab('timetrace')">⏱️ Time Trace</div>
        </div>
        
        <div id="settings" class="tab-content active">
            <h2>Login Settings</h2>
            <div class="input-group">
                <label>Username:</label>
                <input type="text" id="username" placeholder="Enter username" style="width: 300px;">
            </div>
            <div class="input-group">
                <label>Password:</label>
                <input type="password" id="password" placeholder="Enter password" style="width: 300px;">
            </div>
            <div class="input-group">
                <label>Site URL:</label>
                <input type="text" id="url" value="https://tap.dor.ms.gov/" style="width: 300px;">
            </div>
            <div class="input-group">
                <label>Headless Mode:</label>
                <input type="checkbox" id="headless"> Run in background (no browser window)
            </div>
            <button class="success" onclick="saveSettings()">💾 Save Settings</button>

            <div class="stats-box" style="margin-top: 20px;">
                <h3>⏸️ Cooldown (Anti-Detection)</h3>
                <div class="input-group">
                    <label>Work Interval:</label>
                    <input type="number" id="work-interval" value="15" style="width: 80px;" min="1"> minutes
                </div>
                <div class="input-group">
                    <label>Rest Min:</label>
                    <input type="number" id="rest-min" value="2" style="width: 80px;" min="1"> minutes
                </div>
                <div class="input-group">
                    <label>Rest Max:</label>
                    <input type="number" id="rest-max" value="3" style="width: 80px;" min="1"> minutes
                </div>
                <div class="input-group">
                    <label>Enable:</label>
                    <input type="checkbox" id="cooldown-enabled" checked> Enable cooldown breaks
                </div>
                <button class="success" onclick="saveCooldownSettings()">💾 Save Cooldown Settings</button>
            </div>

            <div class="stats-box" style="margin-top: 30px;">
                <h3>📖 Instructions</h3>
                <ol>
                    <li>Enter your login credentials above and save</li>
                    <li>Add items to order in the 'Orders' tab</li>
                    <li>Go to 'Control' tab and click 'Start Bot'</li>
                    <li>For first-time use, complete 2FA manually in the browser</li>
                    <li>The bot will continuously process orders until stopped</li>
                </ol>
            </div>
        </div>
        
        <div id="orders" class="tab-content">
            <h2>Order Items</h2>
            <div style="margin-bottom: 20px;">
                <input type="number" id="item_number" placeholder="Item Number" style="width: 130px;">
                <input type="text" id="item_name" placeholder="Name" style="width: 200px;">
                <input type="text" id="item_size" placeholder="Size" style="width: 80px;">
                <input type="number" id="quantity" placeholder="Quantity" style="width: 100px;">
                <button class="success" onclick="addItem()">➕ Add Item</button>
                <button onclick="loadOrders()">🔄 Refresh</button>
                <button class="danger" onclick="clearCompleted()">🗑️ Clear Completed</button>
                <button class="danger" onclick="clearAllOrders()">🗑️ Clear All</button>
                <button onclick="sortOrders()">🔢 Sort by Item #</button>
                <button onclick="downloadCSV()">📥 Download CSV</button>
                <button onclick="document.getElementById('file-upload').click()">📤 Upload CSV</button>
                <input type="file" id="file-upload" style="display: none;" accept=".csv" onchange="uploadCSV(event)">
            </div>
            <table id="orders-table">
                <thead>
                    <tr>
                        <th>Item Number</th>
                        <th>Name</th>
                        <th>Size</th>
                        <th>Units</th>
                        <th>Quantity</th>
                        <th>Status</th>
                        <th>Actions</th>
                    </tr>
                </thead>
                <tbody></tbody>
            </table>
        </div>
        
        <div id="control" class="tab-content">
            <h2>Bot Control</h2>
            <button id="bot-toggle" class="success" onclick="toggleBot()" style="font-size: 18px; padding: 15px 30px;">▶️ Start Bot</button>
            <button id="skip-cooldown-btn" onclick="skipCooldown()" style="font-size: 14px; padding: 10px 20px; display: none;">⏩ Skip Cooldown</button>
            <div id="status" class="status stopped">⭕ Status: Stopped</div>
            <div class="stats-box">
                <h3>📊 Statistics</h3>
                <div id="stats">Loading...</div>
            </div>
        </div>
        
        <div id="logs" class="tab-content">
            <h2>System Logs</h2>
            <button class="danger" onclick="clearLogs()">🗑️ Clear Logs</button>
            <button onclick="loadLogs()">🔄 Refresh Logs</button>
            <div id="logs-content"></div>
        </div>
        
        <div id="orderdata" class="tab-content">
            <h2>Order Data Lookup</h2>
            <p style="color:#666; margin-bottom:15px;">Select order files to search across. Shows all items with their source file and order date.</p>
            <div style="margin-bottom:15px;">
                <button onclick="loadOrderFiles()">🔄 Refresh Files</button>
                <button class="success" onclick="searchOrderData()">🔍 Search Selected</button>
                <label style="margin-left:15px; cursor:pointer;"><input type="checkbox" id="select-all-files" onchange="toggleAllFiles(this)"> Select All</label>
            </div>
            <div id="order-files-list" style="background:#f0f0f0; padding:10px; border-radius:5px; margin-bottom:20px; max-height:200px; overflow-y:auto;"></div>
            <div style="margin-bottom:10px;">
                <label style="cursor:pointer;"><input type="checkbox" id="include-special-orders" checked> Include Special Orders</label>
                <label style="margin-left:15px; cursor:pointer;"><input type="checkbox" id="include-current-prices" checked> Include Current Prices</label>
                <label style="margin-left:15px; cursor:pointer;"><input type="checkbox" id="include-sales-data" checked> Include Sales Data</label>
            </div>
            <div id="order-data-status" style="margin-bottom:10px; font-weight:bold;"></div>
            <div style="margin-bottom:10px;">
                <input type="text" id="item-search-filter" placeholder="Item #s (comma-separated) or Name/Category..." oninput="filterOrderResults()" onkeydown="if(event.key==='Enter')searchOrderData()" style="width:400px; padding:8px;">
            </div>
            <div style="overflow-x:auto;">
                <table id="order-data-table" style="display:none;">
                    <thead>
                        <tr>
                            <th style="cursor:pointer;" onclick="sortOrderResults('item_num')">Item # ⇅</th>
                            <th style="cursor:pointer;" onclick="sortOrderResults('size')">Size ⇅</th>
                            <th style="cursor:pointer;" onclick="sortOrderResults('units')">Units ⇅</th>
                            <th style="cursor:pointer;" onclick="sortOrderResults('available')">Available ⇅</th>
                            <th>Qty Requested</th>
                            <th style="cursor:pointer;" onclick="sortOrderResults('units_sold')">Units Sold ⇅</th>
                            <th style="cursor:pointer;" onclick="sortOrderResults('qty_on_hand')">Qty On Hand ⇅</th>
                            <th style="cursor:pointer;" onclick="sortOrderResults('name')">Name ⇅</th>
                            <th style="cursor:pointer;" onclick="sortOrderResults('source_file')">Source ⇅</th>
                            <th style="cursor:pointer;" onclick="sortOrderResults('spa_date')">Sale Date ⇅</th>
                            <th>SPA Price</th>
                            <th style="cursor:pointer;" onclick="sortOrderResults('case_cost')">Case Cost ⇅</th>
                            <th>Discount</th>
                            <th>Actions</th>
                        </tr>
                    </thead>
                    <tbody></tbody>
                </table>
            </div>
        </div>
        
        <div id="specialorders" class="tab-content">
            <h2>Special Orders</h2>
            <p style="color:#666; margin-bottom:15px;">Manage special order items. These can be searched from the Order Data tab.</p>
            <div style="margin-bottom:20px;">
                <input type="number" id="so-item-number" placeholder="Item Number" style="width:130px;">
                <input type="number" id="so-quantity" placeholder="Quantity" style="width:100px;">
                <input type="text" id="so-name" placeholder="Name" style="width:200px;">
                <input type="text" id="so-order-number" placeholder="Order Number" style="width:130px;">
                <input type="text" id="so-order-date" placeholder="Order Date" style="width:130px;">
                <button class="success" onclick="addSpecialOrder()">➕ Add</button>
                <button onclick="loadSpecialOrders()">🔄 Refresh</button>
            </div>
            <table id="special-orders-table">
                <thead>
                    <tr>
                        <th>Item Number</th>
                        <th>Quantity</th>
                        <th>Name</th>
                        <th>Order Number</th>
                        <th>Order Date</th>
                        <th>Actions</th>
                    </tr>
                </thead>
                <tbody></tbody>
            </table>
        </div>

        <div id="timetrace" class="tab-content">
            <h2>Time Trace</h2>
            <div class="stats-box" id="trace-summary">Loading...</div>
            <div style="margin: 10px 0;">
                <button class="danger" onclick="clearTraces()">🗑️ Clear Traces</button>
                <button onclick="exportTraces()">📥 Export CSV</button>
            </div>
            <table id="trace-table">
                <thead>
                    <tr>
                        <th>Item #</th>
                        <th>Result</th>
                        <th>Total (ms)</th>
                        <th>Type (ms)</th>
                        <th>Search (ms)</th>
                        <th>Qty Check (ms)</th>
                        <th>Add (ms)</th>
                        <th>Enter Qty (ms)</th>
                        <th>Clear (ms)</th>
                    </tr>
                </thead>
                <tbody></tbody>
            </table>
        </div>
    </div>

    <script>
        let logsInterval;
        let ordersInterval;
        let traceInterval;
        
        function showTab(tabName) {
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
            event.target.classList.add('active');
            document.getElementById(tabName).classList.add('active');
            
            if (tabName === 'logs') {
                loadLogs();
                logsInterval = setInterval(loadLogs, 2000);
            } else {
                clearInterval(logsInterval);
            }
            
            if (tabName === 'orders') {
                loadOrders();
                ordersInterval = setInterval(loadOrders, 5000);
            } else {
                clearInterval(ordersInterval);
            }

            if (tabName === 'timetrace') {
                loadTraces();
                traceInterval = setInterval(loadTraces, 1000);
            } else {
                clearInterval(traceInterval);
            }

            if (tabName === 'orderdata') {
                loadOrderFiles();
            }
            
            if (tabName === 'specialorders') {
                loadSpecialOrders();
            }
        }
        
        function saveSettings() {
            fetch('/save_settings', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    username: document.getElementById('username').value,
                    password: document.getElementById('password').value,
                    url: document.getElementById('url').value,
                    headless: document.getElementById('headless').checked
                })
            }).then(() => {
                alert('✅ Settings saved successfully!');
                loadSettings();
            });
        }
        
        function loadSettings() {
            fetch('/get_settings')
                .then(r => r.json())
                .then(data => {
                    document.getElementById('username').value = data.username || '';
                    document.getElementById('password').value = data.password || '';
                    document.getElementById('url').value = data.url || 'https://tap.dor.ms.gov/';
                    document.getElementById('headless').checked = data.headless || false;
                });
        }
        
        function addItem() {
            const item_number = document.getElementById('item_number').value;
            const name = document.getElementById('item_name').value.trim();
            const size = document.getElementById('item_size').value.trim();
            const quantity = document.getElementById('quantity').value;
            
            if (!item_number || !quantity || !name || !size) {
                alert('Please enter item number, name, size, and quantity');
                return;
            }
            
            fetch('/add_item', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    item_number: item_number,
                    name: name,
                    size: size,
                    quantity: quantity
                })
            }).then(() => {
                document.getElementById('item_number').value = '';
                document.getElementById('item_name').value = '';
                document.getElementById('item_size').value = '';
                document.getElementById('quantity').value = '';
                loadOrders();
            });
        }
        
        function deleteItem(index) {
            if (confirm('Delete this item?')) {
                fetch('/delete_item', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({index: index})
                }).then(() => loadOrders());
            }
        }
        
        function clearCompleted() {
            if (confirm('Clear all completed items?')) {
                fetch('/clear_completed', {method: 'POST'})
                    .then(() => loadOrders());
            }
        }

        function clearAllOrders() {
            if (confirm('Clear ALL orders? This cannot be undone.')) {
                fetch('/clear_all_orders', {method: 'POST'})
                    .then(() => loadOrders());
            }
        }

        function toggleBot() {
            fetch('/toggle_bot', {method: 'POST'})
                .then(r => r.json())
                .then(data => updateStatus(data));
        }
        
        function updateStatus(data) {
            const statusDiv = document.getElementById('status');
            const toggleBtn = document.getElementById('bot-toggle');
            const skipBtn = document.getElementById('skip-cooldown-btn');

            if (data.cooldown) {
                const remaining = Math.ceil(data.cooldown_remaining || 0);
                const mins = Math.floor(remaining / 60);
                const secs = remaining % 60;
                statusDiv.className = 'status cooldown';
                statusDiv.innerHTML = '⏸️ Cooldown: ' + mins + 'm ' + secs + 's remaining';
                toggleBtn.innerHTML = '⏹️ Stop Bot';
                toggleBtn.className = 'danger';
                skipBtn.style.display = 'inline-block';
            } else if (data.running) {
                statusDiv.className = 'status running';
                statusDiv.innerHTML = '✅ Status: Running';
                toggleBtn.innerHTML = '⏹️ Stop Bot';
                toggleBtn.className = 'danger';
                skipBtn.style.display = 'none';
            } else {
                statusDiv.className = 'status stopped';
                statusDiv.innerHTML = '⭕ Status: Stopped';
                toggleBtn.innerHTML = '▶️ Start Bot';
                toggleBtn.className = 'success';
                skipBtn.style.display = 'none';
            }

            loadStats();
        }
        
        function loadOrders() {
            fetch('/get_orders')
                .then(r => r.json())
                .then(data => {
                    const tbody = document.querySelector('#orders-table tbody');
                    tbody.innerHTML = data.map((item, index) => `
                        <tr>
                            <td>${item.item_number}</td>
                            <td>${item.name || ''}</td>
                            <td>${item.size || ''}</td>
                            <td>${item.units || ''}</td>
                            <td>${item.quantity}</td>
                            <td>${item.order_filled === 'yes' ? '✅ Completed' : item.order_filled === 'backorder' ? '📦 Backorder' : '⏳ Pending'}</td>
                            <td>
                                <button class="danger" onclick="deleteItem(${index})">Delete</button>
                            </td>
                        </tr>
                    `).join('');
                    loadStats();
                });
        }
        
        function sortOrders() {
            fetch('/sort_orders', {method: 'POST'})
                .then(r => r.json())
                .then(() => loadOrders());
        }
        
        function loadStats() {
            fetch('/get_stats')
                .then(r => r.json())
                .then(data => {
                    document.getElementById('stats').innerHTML = `
                        <p>📦 Total Items: ${data.total}</p>
                        <p>✅ Completed: ${data.completed}</p>
                        <p>⏳ Remaining: ${data.remaining}</p>
                    `;
                });
        }
        
        function loadLogs() {
            fetch('/get_logs')
                .then(r => r.json())
                .then(data => {
                    const logsDiv = document.getElementById('logs-content');
                    logsDiv.innerHTML = data.logs.map(log => 
                        `<div class="log-entry">${log}</div>`
                    ).join('');
                    logsDiv.scrollTop = logsDiv.scrollHeight;
                });
        }
        
        function clearLogs() {
            if (confirm('Clear all logs?')) {
                fetch('/clear_logs', {method: 'POST'})
                    .then(() => loadLogs());
            }
        }
        
        function downloadCSV() {
            window.location.href = '/download_csv';
        }
        
        function uploadCSV(event) {
            const file = event.target.files[0];
            const formData = new FormData();
            formData.append('file', file);
            
            fetch('/upload_csv', {
                method: 'POST',
                body: formData
            }).then(() => {
                alert('CSV uploaded successfully!');
                loadOrders();
            });
        }
        
        let orderDataResults = [];
        let orderDataSortKey = 'order_date';
        let orderDataSortAsc = false;
        
        function loadOrderFiles() {
            fetch('/get_order_files')
                .then(r => r.json())
                .then(data => {
                    const container = document.getElementById('order-files-list');
                    if (data.files.length === 0) {
                        container.innerHTML = '<p style="color:#999;">No .ods files found in Order Data folder.</p>';
                        return;
                    }
                    container.innerHTML = data.files.map(f => `
                        <label style="display:block; margin:4px 0; cursor:pointer;">
                            <input type="checkbox" class="order-file-cb" value="${f.filename}" checked>
                            ${f.display_name} <span style="color:#888; font-size:12px;">(${f.filename})</span>
                        </label>
                    `).join('');
                    document.getElementById('select-all-files').checked = true;
                });
        }
        
        function toggleAllFiles(cb) {
            document.querySelectorAll('.order-file-cb').forEach(c => c.checked = cb.checked);
        }
        
        function searchOrderData() {
            const selected = Array.from(document.querySelectorAll('.order-file-cb:checked')).map(c => c.value);
            const includeSpecial = document.getElementById('include-special-orders').checked;
            const includeCurrentPrices = document.getElementById('include-current-prices').checked;
            const includeSalesData = document.getElementById('include-sales-data').checked;
            if (selected.length === 0 && !includeSpecial) {
                alert('Please select at least one file or include special orders.');
                return;
            }
            const statusDiv = document.getElementById('order-data-status');
            statusDiv.textContent = 'Searching...';
            
            fetch('/search_order_data', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({files: selected, include_special: includeSpecial, include_current_prices: includeCurrentPrices, include_sales_data: includeSalesData})
            })
            .then(r => r.json())
            .then(data => {
                orderDataResults = data.items;
                orderDataSortKey = 'order_date';
                orderDataSortAsc = false;
                const uniqueItems = new Set(data.items.map(i => i.item_num)).size;
                let src = selected.length + ' file(s)';
                if (includeSpecial) src += ' + specialorder.csv';
                statusDiv.textContent = `Found ${data.items.length} rows (${uniqueItems} unique items) across ${src}.`;
                renderOrderResults();
            })
            .catch(err => {
                statusDiv.textContent = 'Error: ' + err;
            });
        }
        
        function renderOrderResults() {
            const table = document.getElementById('order-data-table');
            const raw = document.getElementById('item-search-filter').value.trim();
            let items = orderDataResults;
            if (raw) {
                const parts = raw.split(',').map(s => s.trim().toLowerCase()).filter(Boolean);
                const numericParts = parts.filter(p => /^[0-9]+$/.test(p));
                if (numericParts.length === parts.length && numericParts.length > 0) {
                    const numSet = new Set(numericParts);
                    items = items.filter(i => numSet.has(i.item_num));
                } else {
                    items = items.filter(i =>
                        parts.some(p =>
                            i.item_num.toLowerCase().includes(p) ||
                            i.name.toLowerCase().includes(p) ||
                            i.category.toLowerCase().includes(p)
                        )
                    );
                }
            }
            const tbody = table.querySelector('tbody');
            tbody.innerHTML = items.map(item => {
                const isSO = item.source_file.startsWith('SO#');
                const hasSale = item.spa_date && item.spa_date !== '';
                let rowStyle = '';
                if (hasSale) rowStyle = 'background:#d4edda; color:#155724;';
                else if (isSO) rowStyle = 'background:#f8d7da; color:#721c24;';
                const sold = parseFloat(item.units_sold) || 0;
                const onHand = parseFloat(item.qty_on_hand) || 0;
                const avg3mo = sold * 3 / 4;
                const lowStock = sold > 0 && onHand > 0 && avg3mo > onHand;
                const qohStyle = lowStock ? 'background:#ff9800; color:#fff; font-weight:bold; padding:2px 6px; border-radius:3px;' : '';
                return `
                <tr style="${rowStyle}">
                    <td>${item.item_num}</td>
                    <td>${item.size || ''}</td>
                    <td>${item.units || ''}</td>
                    <td>${item.available || ''}</td>
                    <td>${item.qty_requested || ''}</td>
                    <td>${item.units_sold || ''}</td>
                    <td><span style="${qohStyle}">${item.qty_on_hand || ''}</span></td>
                    <td>${item.name}</td>
                    <td>${item.source_file}</td>
                    <td>${item.spa_date || ''}</td>
                    <td>${item.spa_price || ''}</td>
                    <td>${item.case_cost || ''}</td>
                    <td>${item.spa_discount || ''}</td>
                    <td><button class="success" style="padding:4px 10px; font-size:12px;" onclick="addToBot('${item.item_num}', '${(item.name||'').replace(/'/g,"\\'")}', '${(item.size||'').replace(/'/g,"\\'")}', '${item.units||''}')">+ Bot</button></td>
                </tr>`;
            }).join('');
            table.style.display = items.length > 0 ? 'table' : 'none';
        }
        
        function filterOrderResults() {
            renderOrderResults();
        }
        
        function sortOrderResults(key) {
            if (orderDataSortKey === key) {
                orderDataSortAsc = !orderDataSortAsc;
            } else {
                orderDataSortKey = key;
                orderDataSortAsc = true;
            }
            orderDataResults.sort((a, b) => {
                let va, vb;
                if (key === 'spa_date') {
                    va = a.spa_sort_date || ''; vb = b.spa_sort_date || '';
                } else {
                    va = a[key] || ''; vb = b[key] || '';
                }
                if (va < vb) return orderDataSortAsc ? -1 : 1;
                if (va > vb) return orderDataSortAsc ? 1 : -1;
                return 0;
            });
            renderOrderResults();
        }
        
        function addToBot(itemNum, name, size, units) {
            const qty = prompt('Enter quantity for item ' + itemNum + ':');
            if (qty === null || qty.trim() === '') return;
            if (isNaN(qty) || parseInt(qty) <= 0) {
                alert('Please enter a valid quantity.');
                return;
            }
            fetch('/add_item', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({item_number: itemNum, name: name || '', size: size || '', units: units || '', quantity: qty.trim()})
            }).then(r => r.json()).then(() => {
                alert('Item ' + itemNum + ' (qty: ' + qty.trim() + ') added to bot orders.');
            });
        }
        
        function loadSpecialOrders() {
            fetch('/get_special_orders')
                .then(r => r.json())
                .then(data => {
                    const tbody = document.querySelector('#special-orders-table tbody');
                    tbody.innerHTML = data.map((item, index) => `
                        <tr>
                            <td>${item.item_number}</td>
                            <td>${item.quantity}</td>
                            <td>${item.name}</td>
                            <td>${item.order_number}</td>
                            <td>${item.order_date}</td>
                            <td>
                                <button class="danger" onclick="deleteSpecialOrder(${index})">Delete</button>
                            </td>
                        </tr>
                    `).join('');
                });
        }
        
        function addSpecialOrder() {
            const item_number = document.getElementById('so-item-number').value;
            const quantity = document.getElementById('so-quantity').value;
            const name = document.getElementById('so-name').value;
            const order_number = document.getElementById('so-order-number').value;
            const order_date = document.getElementById('so-order-date').value;
            
            if (!item_number) {
                alert('Please enter at least an item number.');
                return;
            }
            
            fetch('/add_special_order', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({item_number, quantity, name, order_number, order_date})
            }).then(() => {
                document.getElementById('so-item-number').value = '';
                document.getElementById('so-quantity').value = '';
                document.getElementById('so-name').value = '';
                document.getElementById('so-order-number').value = '';
                document.getElementById('so-order-date').value = '';
                loadSpecialOrders();
            });
        }
        
        function deleteSpecialOrder(index) {
            if (confirm('Delete this special order?')) {
                fetch('/delete_special_order', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({index: index})
                }).then(() => loadSpecialOrders());
            }
        }
        
        function loadTraces() {
            fetch('/get_traces')
                .then(r => r.json())
                .then(data => {
                    const traces = data.traces || [];
                    const tbody = document.querySelector('#trace-table tbody');
                    let added = 0, skipped = 0, failed = 0;
                    let times = [];
                    tbody.innerHTML = traces.map((t, i) => {
                        const result = (t.result || '').toUpperCase();
                        let cls = 'trace-skip';
                        if (result === 'ADDED') { cls = 'trace-added'; added++; }
                        else if (result === 'FAILED') { cls = 'trace-failed'; failed++; }
                        else { skipped++; }
                        const total = t.total_ms || 0;
                        if (total > 0) times.push(total);
                        return '<tr class="' + cls + '">' +
                            '<td>' + (t.item_number || '') + '</td>' +
                            '<td>' + result + '</td>' +
                            '<td>' + total + '</td>' +
                            '<td>' + (t.type_ms || '') + '</td>' +
                            '<td>' + (t.search_ms || '') + '</td>' +
                            '<td>' + (t.qty_check_ms || '') + '</td>' +
                            '<td>' + (t.add_ms || '') + '</td>' +
                            '<td>' + (t.enter_qty_ms || '') + '</td>' +
                            '<td>' + (t.clear_ms || '') + '</td>' +
                            '</tr>';
                    }).join('');
                    const avg = times.length ? Math.round(times.reduce((a,b) => a+b, 0) / times.length) : 0;
                    const fastest = times.length ? Math.min(...times) : 0;
                    const slowest = times.length ? Math.max(...times) : 0;
                    document.getElementById('trace-summary').innerHTML =
                        '<b>Items checked:</b> ' + traces.length +
                        ' | <b>Added:</b> ' + added +
                        ' | <b>Skipped:</b> ' + skipped +
                        ' | <b>Failed:</b> ' + failed +
                        ' | <b>Avg:</b> ' + avg + 'ms' +
                        ' | <b>Fastest:</b> ' + fastest + 'ms' +
                        ' | <b>Slowest:</b> ' + slowest + 'ms';
                });
        }

        function clearTraces() {
            if (confirm('Clear all traces?')) {
                fetch('/clear_traces', {method: 'POST'})
                    .then(() => loadTraces());
            }
        }

        function exportTraces() {
            window.location.href = '/export_traces';
        }

        function skipCooldown() {
            fetch('/skip_cooldown', {method: 'POST'});
        }

        function saveCooldownSettings() {
            fetch('/save_cooldown_settings', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    work_interval: parseInt(document.getElementById('work-interval').value) || 15,
                    rest_min: parseInt(document.getElementById('rest-min').value) || 2,
                    rest_max: parseInt(document.getElementById('rest-max').value) || 3,
                    cooldown_enabled: document.getElementById('cooldown-enabled').checked
                })
            }).then(() => {
                alert('Cooldown settings saved!');
            });
        }

        // Check bot status periodically
        setInterval(() => {
            fetch('/get_status')
                .then(r => r.json())
                .then(data => updateStatus(data));
        }, 3000);

        // Load initial data
        loadSettings();
        loadOrders();
    </script>
</body>
</html>
'''

# Store logs in memory
logs = []

class LogCapture(logging.Handler):
    def emit(self, record):
        global logs
        log_entry = self.format(record)
        logs.append(f"[{record.asctime}] {log_entry}")
        if len(logs) > 100:  # Keep only last 100 logs
            logs.pop(0)

# Set up logging
log_handler = LogCapture()
log_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%H:%M:%S'))
logging.getLogger().addHandler(log_handler)

# Start remote log viewer (http://127.0.0.1:5051) for self-diagnosis
try:
    from log_server import install_log_collector, start_server as start_log_server
    install_log_collector()
    start_log_server()
    logging.getLogger().info("Remote log viewer at http://127.0.0.1:5051")
except Exception as e:
    logging.getLogger().warning(f"Log server failed to start: {e}")

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/save_settings', methods=['POST'])
def save_settings():
    data = request.json
    with open('.env', 'w') as f:
        f.write(f"SITE_USERNAME={data['username']}\n")
        f.write(f"SITE_PASSWORD={data['password']}\n")
        f.write(f"SITE_URL={data['url']}\n")
        f.write(f"HEADLESS={data.get('headless', False)}\n")
    load_dotenv(override=True)
    return jsonify({'success': True})

@app.route('/get_settings')
def get_settings():
    load_dotenv()
    return jsonify({
        'username': os.getenv('SITE_USERNAME', ''),
        'password': os.getenv('SITE_PASSWORD', ''),
        'url': os.getenv('SITE_URL', 'https://tap.dor.ms.gov/'),
        'headless': os.getenv('HEADLESS', 'False').lower() == 'true'
    })

@app.route('/get_orders')
def get_orders():
    return jsonify(_read_csv_locked())

@app.route('/add_item', methods=['POST'])
def add_item():
    data = request.json
    orders = _read_csv_locked()
    orders.append({
        'item_number': data['item_number'],
        'quantity': data['quantity'],
        'name': data.get('name', ''),
        'size': data.get('size', ''),
        'units': data.get('units', ''),
        'order_filled': ''
    })
    _write_csv_locked(orders)
    return jsonify({'success': True})

@app.route('/delete_item', methods=['POST'])
def delete_item():
    data = request.json
    index = data['index']
    orders = _read_csv_locked()
    if 0 <= index < len(orders):
        orders.pop(index)
    _write_csv_locked(orders)
    return jsonify({'success': True})

@app.route('/clear_completed', methods=['POST'])
def clear_completed():
    orders = _read_csv_locked()
    orders = [row for row in orders if row.get('order_filled', '').lower() != 'yes']
    _write_csv_locked(orders)
    return jsonify({'success': True})

@app.route('/sort_orders', methods=['POST'])
def sort_orders():
    orders = _read_csv_locked()
    orders.sort(key=lambda x: int(x.get('item_number', 0) or 0))
    _write_csv_locked(orders)
    return jsonify({'success': True})

@app.route('/get_stats')
def get_stats():
    orders = _read_csv_locked()
    total = len(orders)
    completed = sum(1 for o in orders if o.get('order_filled', '').lower() == 'yes')
    return jsonify({
        'total': total,
        'completed': completed,
        'remaining': total - completed
    })

@app.route('/toggle_bot', methods=['POST'])
def toggle_bot():
    global bot_running, bot_thread
    
    if not bot_running:
        # Validate settings first
        load_dotenv()
        if not os.getenv('SITE_USERNAME') or not os.getenv('SITE_PASSWORD'):
            return jsonify({'error': 'Please configure settings first'}), 400
        
        bot_running = True
        stop_event.clear()
        bot_thread = threading.Thread(target=run_bot_thread, daemon=True)
        bot_thread.start()
        logging.info("Bot started")
    else:
        bot_running = False
        stop_event.set()
        logging.info("Bot stopped")
    
    return jsonify({'running': bot_running})

@app.route('/get_status')
def get_status():
    return jsonify({
        'running': bot_running,
        'cooldown': cooldown_active,
        'cooldown_remaining': max(0, cooldown_end - time.time())
    })

@app.route('/get_logs')
def get_logs():
    global logs
    return jsonify({'logs': logs[-50:]})  # Return last 50 logs

@app.route('/clear_logs', methods=['POST'])
def clear_logs():
    global logs
    logs = []
    return jsonify({'success': True})

@app.route('/download_csv')
def download_csv():
    if Path('orders.csv').exists():
        return send_file('orders.csv', as_attachment=True)
    return "No CSV file found", 404

@app.route('/upload_csv', methods=['POST'])
def upload_csv():
    file = request.files['file']
    if file and file.filename.endswith('.csv'):
        file.save('orders.csv')
        return jsonify({'success': True})
    return jsonify({'error': 'Invalid file'}), 400

@app.route('/clear_all_orders', methods=['POST'])
def clear_all_orders():
    _write_csv_locked([])
    return jsonify({'success': True})

@app.route('/get_traces')
def get_traces():
    traces = []
    if current_bot and hasattr(current_bot, 'trace_log'):
        traces = current_bot.trace_log
    return jsonify({'traces': traces})

@app.route('/clear_traces', methods=['POST'])
def clear_traces():
    if current_bot and hasattr(current_bot, 'trace_log'):
        current_bot.trace_log.clear()
    return jsonify({'success': True})

@app.route('/export_traces')
def export_traces():
    traces = []
    if current_bot and hasattr(current_bot, 'trace_log'):
        traces = current_bot.trace_log
    output = io.StringIO()
    fields = ['item_number', 'result', 'total_ms', 'type_ms', 'search_ms', 'qty_check_ms', 'add_ms', 'enter_qty_ms', 'clear_ms']
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction='ignore')
    writer.writeheader()
    for t in traces:
        writer.writerow(t)
    output.seek(0)
    return send_file(
        io.BytesIO(output.getvalue().encode('utf-8')),
        mimetype='text/csv',
        as_attachment=True,
        download_name='traces.csv'
    )

@app.route('/skip_cooldown', methods=['POST'])
def skip_cooldown_route():
    global skip_cooldown_flag
    skip_cooldown_flag = True
    return jsonify({'success': True})

@app.route('/save_cooldown_settings', methods=['POST'])
def save_cooldown_settings():
    global cooldown_enabled, work_interval, rest_min, rest_max
    data = request.json
    work_interval = data.get('work_interval', 15)
    rest_min = data.get('rest_min', 2)
    rest_max = data.get('rest_max', 3)
    cooldown_enabled = data.get('cooldown_enabled', True)
    return jsonify({'success': True})

ORDER_DATA_DIR = Path('Order Data')

def parse_date_from_filename(filename):
    """Extract date from filenames like 'R301542-2-16-26.ods' (M-DD-YY)."""
    match = re.search(r'(\d{1,2})-(\d{1,2})-(\d{2,4})', filename)
    if match:
        try:
            m, d, y = match.group(1), match.group(2), match.group(3)
            if len(y) == 2:
                y = '20' + y
            return datetime(int(y), int(m), int(d))
        except (ValueError, TypeError):
            pass
    return None

def read_ods_file(filepath):
    """Read an ODS file. Returns (date, list_of_item_dicts)."""
    doc = load_ods(str(filepath))
    dt = parse_date_from_filename(filepath.name)
    items = []
    for sheet in doc.body.getElementsByType(Table):
        rows = sheet.getElementsByType(TableRow)
        if not rows:
            continue
        for row in rows[1:]:
            cells = row.getElementsByType(TableCell)
            vals = []
            for cell in cells:
                repeat = int(cell.getAttribute('numbercolumnsrepeated') or 1)
                ps = cell.getElementsByType(P)
                text = ''.join(p.firstChild.data if p.firstChild else '' for p in ps)
                vals.extend([text] * min(repeat, 20))
            if len(vals) >= 9 and vals[0].strip():
                items.append({
                    'item_num': vals[0].strip(),
                    'name': vals[1].strip(),
                    'category': vals[2].strip(),
                    'pkg_type': vals[3].strip(),
                    'units': vals[4].strip(),
                    'price': vals[5].strip(),
                    'qty_requested': vals[6].strip(),
                    'qty_reserved': vals[7].strip(),
                    'sub_total': vals[8].strip(),
                })
    return dt, items

@app.route('/get_order_files')
def get_order_files():
    if not ORDER_DATA_DIR.exists():
        return jsonify({'files': []})
    files = []
    for f in sorted(ORDER_DATA_DIR.iterdir()):
        if f.suffix.lower() == '.ods':
            dt = parse_date_from_filename(f.name)
            display = dt.strftime('%b %d, %Y') if dt else f.stem
            files.append({
                'filename': f.name,
                'display_name': display,
                'sort_key': dt.isoformat() if dt else '',
            })
    files.sort(key=lambda x: x['sort_key'], reverse=True)
    return jsonify({'files': files})

SPECIAL_ORDER_CSV = Path('specialorder.csv')
SPECIAL_ORDER_FIELDS = ['item_number', 'quantity', 'name', 'order_number', 'order_date']

def _read_special_orders():
    orders = []
    if SPECIAL_ORDER_CSV.exists():
        with open(SPECIAL_ORDER_CSV, 'r', newline='') as f:
            reader = csv.DictReader(f)
            orders = list(reader)
    return orders

def _write_special_orders(orders):
    with open(SPECIAL_ORDER_CSV, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=SPECIAL_ORDER_FIELDS)
        writer.writeheader()
        writer.writerows(orders)

@app.route('/get_special_orders')
def get_special_orders():
    return jsonify(_read_special_orders())

@app.route('/add_special_order', methods=['POST'])
def add_special_order():
    data = request.json
    orders = _read_special_orders()
    orders.append({
        'item_number': data.get('item_number', ''),
        'quantity': data.get('quantity', ''),
        'name': data.get('name', ''),
        'order_number': data.get('order_number', ''),
        'order_date': data.get('order_date', ''),
    })
    _write_special_orders(orders)
    return jsonify({'success': True})

@app.route('/delete_special_order', methods=['POST'])
def delete_special_order():
    index = request.json['index']
    orders = _read_special_orders()
    if 0 <= index < len(orders):
        orders.pop(index)
    _write_special_orders(orders)
    return jsonify({'success': True})

FUTURE_SPA_DIR = ORDER_DATA_DIR / 'FutureSPA'
CURRENT_PRICES_DIR = ORDER_DATA_DIR / 'CurrentPrices'
SALES_DATA_DIR = ORDER_DATA_DIR / 'SalesData'

def _load_current_prices():
    """Load CurrentPrices data into a dict keyed by item code."""
    prices = {}
    if not CURRENT_PRICES_DIR.exists():
        return prices
    for f in CURRENT_PRICES_DIR.iterdir():
        if f.suffix.lower() != '.ods':
            continue
        try:
            doc = load_ods(str(f))
        except Exception:
            continue
        for sheet in doc.body.getElementsByType(Table):
            rows = sheet.getElementsByType(TableRow)
            if not rows:
                continue
            for row in rows[1:]:
                cells = row.getElementsByType(TableCell)
                vals = []
                for cell in cells:
                    repeat = int(cell.getAttribute('numbercolumnsrepeated') or 1)
                    ps = cell.getElementsByType(P)
                    text = ''.join(p.firstChild.data if p.firstChild else '' for p in ps)
                    vals.extend([text] * min(repeat, 20))
                if len(vals) >= 4 and vals[0].strip():
                    item_code = vals[0].strip()
                    prices[item_code] = {
                        'name': vals[1].strip() if len(vals) > 1 else '',
                        'size': vals[2].strip() if len(vals) > 2 else '',
                        'available': vals[3].strip() if len(vals) > 3 else '',
                        'units': vals[5].strip() if len(vals) > 5 else '',
                        'case_cost': vals[6].strip() if len(vals) > 6 else '',
                    }
    return prices

def _load_sales_data():
    """Load SalesData from .xls files (HTML tables saved as .xls) keyed by item number."""
    from html.parser import HTMLParser

    class TableParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.rows = []
            self.current_row = None
            self.current_cell = ''
            self.in_cell = False
        def handle_starttag(self, tag, attrs):
            if tag == 'tr':
                self.current_row = []
            elif tag in ('td', 'th'):
                self.in_cell = True
                self.current_cell = ''
        def handle_endtag(self, tag):
            if tag in ('td', 'th') and self.in_cell:
                self.in_cell = False
                if self.current_row is not None:
                    self.current_row.append(self.current_cell.strip())
            elif tag == 'tr' and self.current_row is not None:
                self.rows.append(self.current_row)
                self.current_row = None
        def handle_data(self, data):
            if self.in_cell:
                self.current_cell += data

    sales = {}
    if not SALES_DATA_DIR.exists():
        return sales
    for f in SALES_DATA_DIR.iterdir():
        if f.suffix.lower() not in ('.xls', '.html', '.htm'):
            continue
        try:
            with open(f, 'r', errors='ignore') as fh:
                content = fh.read()
        except Exception:
            continue
        parser = TableParser()
        parser.feed(content)
        header_idx = None
        for i, row in enumerate(parser.rows):
            if any('Item #' in c or 'Item#' in c for c in row):
                header_idx = i
                break
        if header_idx is None:
            continue
        for row in parser.rows[header_idx + 1:]:
            if len(row) < 10:
                continue
            item_num = row[1].strip()
            if not item_num or not any(c.isdigit() for c in item_num):
                continue
            name = row[3].strip() if len(row) > 3 else ''
            units_sold = row[4].strip() if len(row) > 4 else ''
            qty_oh = row[9].strip() if len(row) > 9 else ''
            sales[item_num] = {
                'name': name,
                'units_sold': units_sold,
                'qty_on_hand': qty_oh,
            }
    return sales

def _load_future_spa():
    """Load FutureSPA data into a dict keyed by item code."""
    spa = {}
    if not FUTURE_SPA_DIR.exists():
        return spa
    for f in FUTURE_SPA_DIR.iterdir():
        if f.suffix.lower() != '.ods':
            continue
        try:
            doc = load_ods(str(f))
        except Exception:
            continue
        for sheet in doc.body.getElementsByType(Table):
            rows = sheet.getElementsByType(TableRow)
            if not rows:
                continue
            for row in rows[1:]:
                cells = row.getElementsByType(TableCell)
                vals = []
                for cell in cells:
                    repeat = int(cell.getAttribute('numbercolumnsrepeated') or 1)
                    ps = cell.getElementsByType(P)
                    text = ''.join(p.firstChild.data if p.firstChild else '' for p in ps)
                    vals.extend([text] * min(repeat, 20))
                if len(vals) >= 9 and vals[1].strip():
                    item_code = vals[1].strip()
                    spa[item_code] = {
                        'name': vals[2].strip(),
                        'spa_date': vals[5].strip(),
                        'spa_price': vals[7].strip(),
                        'spa_discount': vals[8].strip(),
                    }
    return spa

@app.route('/search_order_data', methods=['POST'])
def search_order_data():
    selected = request.json.get('files', [])
    include_special = request.json.get('include_special', False)
    include_current_prices = request.json.get('include_current_prices', False)
    include_sales_data = request.json.get('include_sales_data', False)

    spa_lookup = _load_future_spa()
    prices_lookup = _load_current_prices() if include_current_prices else {}
    sales_lookup = _load_sales_data() if include_sales_data else {}

    all_rows = []
    for fname in selected:
        fpath = ORDER_DATA_DIR / fname
        if not fpath.exists():
            continue
        try:
            dt, rows = read_ods_file(fpath)
        except Exception:
            continue
        date_str = dt.strftime('%b %d, %Y') if dt else fname
        sort_date = dt.isoformat() if dt else ''
        for row in rows:
            row['sort_date'] = sort_date
            row['source_file'] = fname
            spa = spa_lookup.get(row['item_num'], {})
            row['spa_date'] = spa.get('spa_date', '')
            row['spa_price'] = spa.get('spa_price', '')
            row['spa_discount'] = spa.get('spa_discount', '')
            row['spa_sort_date'] = spa.get('spa_date', '')
            cp = prices_lookup.get(row['item_num'], {})
            row['size'] = cp.get('size', '')
            row['units'] = cp.get('units', '')
            row['available'] = cp.get('available', '')
            row['case_cost'] = cp.get('case_cost', '')
            sd = sales_lookup.get(row['item_num'], {})
            row['units_sold'] = sd.get('units_sold', '')
            row['qty_on_hand'] = sd.get('qty_on_hand', '')
            all_rows.append(row)

    if include_special and SPECIAL_ORDER_CSV.exists():
        for srow in _read_special_orders():
            item_num = srow.get('item_number', '').strip()
            if not item_num:
                continue
            spa = spa_lookup.get(item_num, {})
            cp = prices_lookup.get(item_num, {})
            sd = sales_lookup.get(item_num, {})
            all_rows.append({
                'item_num': item_num,
                'name': srow.get('name', '').strip(),
                'qty_requested': '',
                'sort_date': '',
                'source_file': 'SO#' + srow.get('order_number', '').strip(),
                'spa_date': spa.get('spa_date', ''),
                'spa_price': spa.get('spa_price', ''),
                'spa_discount': spa.get('spa_discount', ''),
                'spa_sort_date': spa.get('spa_date', ''),
                'size': cp.get('size', ''),
                'units': cp.get('units', ''),
                'available': cp.get('available', ''),
                'case_cost': cp.get('case_cost', ''),
                'units_sold': sd.get('units_sold', ''),
                'qty_on_hand': sd.get('qty_on_hand', ''),
            })

    seen_items = set(row['item_num'] for row in all_rows)
    for item_code, spa in spa_lookup.items():
        if item_code not in seen_items:
            cp = prices_lookup.get(item_code, {})
            sd = sales_lookup.get(item_code, {})
            all_rows.append({
                'item_num': item_code,
                'name': spa.get('name', ''),
                'qty_requested': '',
                'sort_date': '',
                'source_file': 'SPA Only',
                'spa_date': spa.get('spa_date', ''),
                'spa_price': spa.get('spa_price', ''),
                'spa_discount': spa.get('spa_discount', ''),
                'spa_sort_date': spa.get('spa_date', ''),
                'size': cp.get('size', ''),
                'units': cp.get('units', ''),
                'available': cp.get('available', ''),
                'case_cost': cp.get('case_cost', ''),
                'units_sold': sd.get('units_sold', ''),
                'qty_on_hand': sd.get('qty_on_hand', ''),
            })
            seen_items.add(item_code)

    all_supplementary = set()
    if include_current_prices:
        all_supplementary.update(prices_lookup.keys())
    if include_sales_data:
        all_supplementary.update(sales_lookup.keys())
    for item_code in all_supplementary:
        if item_code not in seen_items:
            cp = prices_lookup.get(item_code, {})
            sd = sales_lookup.get(item_code, {})
            spa = spa_lookup.get(item_code, {})
            name = cp.get('name', '') or sd.get('name', '') or spa.get('name', '')
            all_rows.append({
                'item_num': item_code,
                'name': name,
                'qty_requested': '',
                'sort_date': '',
                'source_file': '',
                'spa_date': spa.get('spa_date', ''),
                'spa_price': spa.get('spa_price', ''),
                'spa_discount': spa.get('spa_discount', ''),
                'spa_sort_date': spa.get('spa_date', ''),
                'size': cp.get('size', ''),
                'units': cp.get('units', ''),
                'available': cp.get('available', ''),
                'case_cost': cp.get('case_cost', ''),
                'units_sold': sd.get('units_sold', ''),
                'qty_on_hand': sd.get('qty_on_hand', ''),
            })
            seen_items.add(item_code)

    all_rows.sort(key=lambda x: x.get('sort_date', ''), reverse=True)
    return jsonify({'items': all_rows})

def run_bot_thread():
    """Run the bot in a separate thread, reusing bot_script.py logic."""
    global bot_running, current_bot, cooldown_active, cooldown_end, skip_cooldown_flag
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        from bot_script import WebAutomationBot, read_csv_file, update_csv_file

        async def run_bot():
            global bot_running, current_bot, cooldown_active, cooldown_end, skip_cooldown_flag
            load_dotenv()
            headless = os.getenv('HEADLESS', 'False').lower() == 'true'
            bot = WebAutomationBot(headless=headless)
            current_bot = bot

            if not Path('orders.csv').exists():
                _write_csv_locked([])
                logging.info("Created empty orders.csv")

            try:
                await bot.setup(use_saved_auth=True)
                logging.info("Bot ready. If prompted for OTP, complete it in the browser.")

                consecutive_errors = 0
                on_item_entry = False
                last_cooldown = time.time()
                while not stop_event.is_set():
                    # Cooldown check
                    if cooldown_enabled and (time.time() - last_cooldown) >= work_interval * 60:
                        rest_duration = random.uniform(rest_min * 60, rest_max * 60)
                        cooldown_active = True
                        cooldown_end = time.time() + rest_duration
                        skip_cooldown_flag = False
                        logging.info(f"Cooldown started: resting for {rest_duration/60:.1f} minutes")
                        while time.time() < cooldown_end and not stop_event.is_set() and not skip_cooldown_flag:
                            await asyncio.sleep(1)
                        cooldown_active = False
                        skip_cooldown_flag = False
                        last_cooldown = time.time()
                        if stop_event.is_set():
                            break
                        logging.info("Cooldown ended, resuming...")

                    try:
                        items = read_csv_file('orders.csv')
                        unfilled = [i for i in items if i.get('order_filled', '').lower() != 'yes']

                        if not unfilled:
                            logging.info("All items completed! Checking for new items in 5 seconds...")
                            await asyncio.sleep(5)
                            on_item_entry = False
                            continue

                        if not on_item_entry or 'itemEntry' not in bot.page.url:
                            await bot.start_order()
                            on_item_entry = True

                        logging.info(f"Checking {len(unfilled)} items for availability...")
                        items_found, total_qty_added = await bot.check_and_process_items(items)
                        consecutive_errors = 0

                        if items_found and total_qty_added >= 10:
                            item_numbers = [str(i['item_number']) for i in items_found]
                            logging.info(f"Found {len(items_found)} items, {total_qty_added} total qty: {', '.join(item_numbers)}")
                            await bot.submit_order()
                            update_csv_file('orders.csv', items)
                            on_item_entry = False
                            remaining = [i for i in items if i.get('order_filled', '').lower() not in ('yes',)]
                            if not remaining:
                                logging.info("All items filled!")
                            else:
                                logging.info(f"{len(remaining)} items remaining — restarting with manual entry...")
                                await asyncio.sleep(2)
                                await bot.start_order()
                                on_item_entry = True
                        elif items_found and total_qty_added < 10:
                            # Keep items in cart — don't revert. Wait for more to become available.
                            logging.warning(f"Have {total_qty_added} qty in cart (need 10 min). Keeping cart, re-checking in 30s...")
                            await asyncio.sleep(30)
                        else:
                            logging.info("No items available. Re-checking...")
                            await asyncio.sleep(1)

                    except Exception as e:
                        consecutive_errors += 1
                        logging.error(f"Error (attempt {consecutive_errors}): {e}")

                        if consecutive_errors < 3:
                            try:
                                await bot.start_order()
                                on_item_entry = True
                                logging.info("Recovered, resuming...")
                                continue
                            except Exception:
                                pass

                        logging.info("Re-initializing bot (full login)...")
                        consecutive_errors = 0
                        on_item_entry = False
                        try:
                            await bot.cleanup()
                        except Exception:
                            pass
                        bot = WebAutomationBot(headless=headless)
                        current_bot = bot
                        try:
                            await bot.setup(use_saved_auth=True)
                            logging.info("Bot recovered successfully")
                        except Exception as setup_err:
                            logging.error(f"Recovery failed: {setup_err}")
                            await asyncio.sleep(5)

            except Exception as e:
                logging.error(f"Bot startup error: {e}")
            finally:
                try:
                    await bot.cleanup()
                except Exception:
                    pass
                current_bot = None

        loop.run_until_complete(run_bot())
    except Exception as e:
        logging.error(f"Thread error: {e}")
    finally:
        loop.close()
        bot_running = False
        cooldown_active = False
        # Reset backorders to pending for next run
        try:
            orders = _read_csv_locked()
            changed = False
            for o in orders:
                if o.get('order_filled', '').lower() == 'backorder':
                    o['order_filled'] = ''
                    changed = True
            if changed:
                _write_csv_locked(orders)
                logging.info("Reset backorder items to pending")
        except Exception:
            pass

if __name__ == '__main__':
    print("\n" + "="*50)
    print("Mississippi DOR Order Bot - Web Interface")
    print("="*50)
    print("\nStarting web server...")
    print("\n🌐 Open your browser and go to: http://localhost:5050")
    print("\nPress Ctrl+C to stop the server\n")
    
    import webbrowser
    webbrowser.open('http://localhost:5050')
    
    app.run(debug=False, port=5050, host='0.0.0.0')