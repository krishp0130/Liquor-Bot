import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, filedialog
import threading
import asyncio
from playwright.async_api import async_playwright
import os
from dotenv import load_dotenv
import logging
from typing import Optional
from pathlib import Path
import csv
import time
import sys
from datetime import datetime
import subprocess
# import bot class
from bot_script import WebAutomationBot, read_csv_file, update_csv_file  # noqa: F401 - used in run_bot

# set up logging to capture to both file and GUI
class GuiLogHandler(logging.Handler):
    def __init__(self, text_widget):
        super().__init__()
        self.text_widget = text_widget
    
    def emit(self, record):
        msg = self.format(record)
        def append():
            self.text_widget.configure(state='normal')
            self.text_widget.insert(tk.END, msg + '\n')
            self.text_widget.configure(state='disabled')
            self.text_widget.see(tk.END)
        # thread-safe GUI update
        self.text_widget.after(0, append)

class BotGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Mississippi DOR Order Bot")
        self.root.geometry("900x700")
        
        # variables
        self.bot_running = False
        self.bot_thread = None
        self.bot = None
        self.stop_event = threading.Event()
        
        # create main frames
        self.create_widgets()
        
        # load environment variables if they exist
        self.load_env_settings()
        
        # check for CSV file
        self.check_csv_file()
        
    def create_widgets(self):
        # create notebook for tabs
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill='both', expand=True, padx=10, pady=5)
        
        # settings tab
        settings_frame = ttk.Frame(notebook)
        notebook.add(settings_frame, text='Settings')
        self.create_settings_tab(settings_frame)
        
        # csv editor tab
        csv_frame = ttk.Frame(notebook)
        notebook.add(csv_frame, text='Orders (CSV)')
        self.create_csv_tab(csv_frame)
        
        # bot control tab
        control_frame = ttk.Frame(notebook)
        notebook.add(control_frame, text='Bot Control')
        self.create_control_tab(control_frame)
        
        # time trace tab
        trace_frame = ttk.Frame(notebook)
        notebook.add(trace_frame, text='Time Trace')
        self.create_trace_tab(trace_frame)

        # log tab
        log_frame = ttk.Frame(notebook)
        notebook.add(log_frame, text='Logs')
        self.create_log_tab(log_frame)
        
    def create_settings_tab(self, parent):
        # frame for settings
        settings_frame = ttk.LabelFrame(parent, text="Login Settings", padding=10)
        settings_frame.pack(fill='both', expand=True, padx=10, pady=10)
        
        # username
        ttk.Label(settings_frame, text="Username:").grid(row=0, column=0, sticky='w', pady=5)
        self.username_entry = ttk.Entry(settings_frame, width=40)
        self.username_entry.grid(row=0, column=1, pady=5, padx=5)
        
        # password
        ttk.Label(settings_frame, text="Password:").grid(row=1, column=0, sticky='w', pady=5)
        self.password_entry = ttk.Entry(settings_frame, width=40, show='*')
        self.password_entry.grid(row=1, column=1, pady=5, padx=5)
        
        # site url
        ttk.Label(settings_frame, text="Site URL:").grid(row=2, column=0, sticky='w', pady=5)
        self.url_entry = ttk.Entry(settings_frame, width=40)
        self.url_entry.grid(row=2, column=1, pady=5, padx=5)
        self.url_entry.insert(0, "https://tap.dor.ms.gov/")
        
        # headless mode checkbox
        self.headless_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(settings_frame, text="Run in background (headless mode)", 
                       variable=self.headless_var).grid(row=3, column=0, columnspan=2, pady=10)
        
        # use saved auth checkbox
        self.use_saved_auth_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(settings_frame, text="Use saved authentication (skip login if possible)",
                       variable=self.use_saved_auth_var).grid(row=4, column=0, columnspan=2, pady=5)

        # cooldown settings
        cooldown_frame = ttk.LabelFrame(parent, text="Cooldown (Anti-Detection)", padding=10)
        cooldown_frame.pack(fill='x', padx=10, pady=(10, 0))

        ttk.Label(cooldown_frame, text="Work interval (min):").grid(row=0, column=0, sticky='w', pady=5)
        self.work_interval_entry = ttk.Entry(cooldown_frame, width=10)
        self.work_interval_entry.grid(row=0, column=1, pady=5, padx=5, sticky='w')
        self.work_interval_entry.insert(0, "15")

        ttk.Label(cooldown_frame, text="Rest min (min):").grid(row=0, column=2, sticky='w', pady=5, padx=(15, 0))
        self.rest_min_entry = ttk.Entry(cooldown_frame, width=10)
        self.rest_min_entry.grid(row=0, column=3, pady=5, padx=5, sticky='w')
        self.rest_min_entry.insert(0, "2")

        ttk.Label(cooldown_frame, text="Rest max (min):").grid(row=0, column=4, sticky='w', pady=5, padx=(15, 0))
        self.rest_max_entry = ttk.Entry(cooldown_frame, width=10)
        self.rest_max_entry.grid(row=0, column=5, pady=5, padx=5, sticky='w')
        self.rest_max_entry.insert(0, "3")

        self.cooldown_enabled_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(cooldown_frame, text="Enable cooldown breaks",
                       variable=self.cooldown_enabled_var).grid(row=1, column=0, columnspan=4, pady=5, sticky='w')

        ttk.Label(cooldown_frame, text="Bot pauses for a random duration between Rest min and Rest max\n"
                  "after every Work interval to avoid detection.",
                  foreground='gray', font=('Arial', 9)).grid(row=2, column=0, columnspan=6, pady=(0, 5), sticky='w')

        # save settings button
        ttk.Button(settings_frame, text="Save Settings",
                  command=self.save_settings).grid(row=5, column=0, columnspan=2, pady=20)
        
        # instructions
        instructions = ttk.LabelFrame(parent, text="Instructions", padding=10)
        instructions.pack(fill='both', padx=10, pady=10)
        
        inst_text = """1. Enter your login credentials and save settings
2. Add items to order in the 'Orders (CSV)' tab
3. Go to 'Bot Control' tab and click 'Start Bot'
4. For first-time use, complete 2FA manually when prompted
5. The bot will continuously process orders until stopped"""
        
        ttk.Label(instructions, text=inst_text, justify='left').pack()

        # software update
        update_frame = ttk.LabelFrame(parent, text="Software Update", padding=10)
        update_frame.pack(fill='x', padx=10, pady=(10, 10))

        ttk.Label(update_frame, text="Operating System:").grid(row=0, column=0, sticky='w', pady=5)
        self.update_os_var = tk.StringVar(value='mac')
        os_combo = ttk.Combobox(update_frame, textvariable=self.update_os_var,
                                values=['mac', 'windows'], state='readonly', width=12)
        os_combo.grid(row=0, column=1, pady=5, padx=5, sticky='w')

        self.update_btn = ttk.Button(update_frame, text="Update Bot", command=self._run_update)
        self.update_btn.grid(row=0, column=2, padx=10)

        self.update_output = scrolledtext.ScrolledText(update_frame, height=8, state='disabled',
                                                        wrap='word', font=('Courier', 10))
        self.update_output.grid(row=1, column=0, columnspan=3, sticky='we', pady=(5, 0))
        update_frame.columnconfigure(0, weight=0)
        update_frame.columnconfigure(1, weight=0)
        update_frame.columnconfigure(2, weight=1)

    def _run_update(self):
        if not messagebox.askyesno("Confirm Update",
                                   "This will pull the latest code and re-install dependencies. Continue?"):
            return
        self.update_btn.configure(state='disabled', text='Updating...')
        self.update_output.configure(state='normal')
        self.update_output.delete('1.0', tk.END)
        self.update_output.insert(tk.END, 'Starting update...\n')
        self.update_output.configure(state='disabled')
        threading.Thread(target=self._run_update_thread, daemon=True).start()

    def _run_update_thread(self):
        bot_dir = Path(__file__).resolve().parent
        target_os = self.update_os_var.get()
        if target_os == 'windows':
            script = bot_dir / 'install-windows.ps1'
            cmd = ['powershell', '-ExecutionPolicy', 'Bypass', '-File', str(script)]
        else:
            script = bot_dir / 'install-mac.sh'
            cmd = ['bash', str(script)]

        if not script.exists():
            self._append_update_output(f'Script not found: {script}\n')
            self.root.after(0, lambda: self.update_btn.configure(state='normal', text='Update Bot'))
            return

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300, cwd=str(bot_dir))
            output = result.stdout
            if result.stderr:
                output += '\n--- stderr ---\n' + result.stderr
            if result.returncode == 0:
                output += '\n✅ Update complete! Restart the bot to use the new version.'
            else:
                output += '\n❌ Update failed. See output above.'
            self._append_update_output(output)
        except subprocess.TimeoutExpired:
            self._append_update_output('Update timed out after 5 minutes.\n')
        except Exception as e:
            self._append_update_output(f'Error running update: {e}\n')
        self.root.after(0, lambda: self.update_btn.configure(state='normal', text='Update Bot'))

    def _append_update_output(self, text):
        def _do():
            self.update_output.configure(state='normal')
            self.update_output.insert(tk.END, text)
            self.update_output.see(tk.END)
            self.update_output.configure(state='disabled')
        self.root.after(0, _do)

    def create_csv_tab(self, parent):
        # frame for csv controls
        control_frame = ttk.Frame(parent)
        control_frame.pack(fill='x', padx=10, pady=10)
        
        ttk.Button(control_frame, text="Load CSV", command=self.load_csv).pack(side='left', padx=5)
        ttk.Button(control_frame, text="Save CSV", command=self.save_csv).pack(side='left', padx=5)
        ttk.Button(control_frame, text="Add Row", command=self.add_csv_row).pack(side='left', padx=5)
        ttk.Button(control_frame, text="Delete Row", command=self.delete_csv_row).pack(side='left', padx=5)
        ttk.Button(control_frame, text="Clear All 'Yes' Marks", command=self.clear_yes_marks).pack(side='left', padx=5)
        ttk.Button(control_frame, text="Clear All Orders", command=self.clear_all_orders).pack(side='left', padx=5)
        
        # frame for csv table
        table_frame = ttk.Frame(parent)
        table_frame.pack(fill='both', expand=True, padx=10, pady=10)
        
        # create treeview for csv
        columns = ('Item Number', 'Quantity', 'Order Filled')
        self.csv_tree = ttk.Treeview(table_frame, columns=columns, show='headings', height=15)
        
        # define headings
        for col in columns:
            self.csv_tree.heading(col, text=col)
            self.csv_tree.column(col, width=150)
        
        # scrollbar
        scrollbar = ttk.Scrollbar(table_frame, orient='vertical', command=self.csv_tree.yview)
        scrollbar.pack(side='right', fill='y')
        self.csv_tree.configure(yscrollcommand=scrollbar.set)
        
        self.csv_tree.pack(side='left', fill='both', expand=True)
        
        # entry frame for adding items
        entry_frame = ttk.LabelFrame(parent, text="Add/Edit Item", padding=10)
        entry_frame.pack(fill='x', padx=10, pady=10)
        
        ttk.Label(entry_frame, text="Item Number:").grid(row=0, column=0, padx=5)
        self.item_entry = ttk.Entry(entry_frame, width=20)
        self.item_entry.grid(row=0, column=1, padx=5)
        
        ttk.Label(entry_frame, text="Quantity:").grid(row=0, column=2, padx=5)
        self.quantity_entry = ttk.Entry(entry_frame, width=20)
        self.quantity_entry.grid(row=0, column=3, padx=5)
        
        ttk.Button(entry_frame, text="Add Item", command=self.add_item).grid(row=0, column=4, padx=20)
        
    def create_control_tab(self, parent):
        # bot control frame
        control_frame = ttk.LabelFrame(parent, text="Bot Control", padding=20)
        control_frame.pack(fill='x', padx=10, pady=10)
        
        # button row
        btn_row = ttk.Frame(control_frame)
        btn_row.pack(pady=10)

        self.start_button = ttk.Button(btn_row, text="Start Bot",
                                      command=self.toggle_bot, state='normal')
        self.start_button.pack(side='left', padx=5)

        self.skip_cooldown_button = ttk.Button(btn_row, text="Skip Cooldown",
                                               command=self.skip_cooldown, state='disabled')
        self.skip_cooldown_button.pack(side='left', padx=5)

        # status label
        self.status_label = ttk.Label(control_frame, text="Status: Stopped",
                                     font=('Arial', 12, 'bold'))
        self.status_label.pack(pady=10)
        
        # statistics frame
        stats_frame = ttk.LabelFrame(parent, text="Statistics", padding=10)
        stats_frame.pack(fill='x', padx=10, pady=10)
        
        self.stats_label = ttk.Label(stats_frame, text="Items Processed: 0\nItems Remaining: 0\nOrders Submitted: 0")
        self.stats_label.pack()
        
        # progress bar
        self.progress = ttk.Progressbar(parent, mode='indeterminate')
        self.progress.pack(fill='x', padx=10, pady=10)
        
    def create_trace_tab(self, parent):
        # summary frame at top
        summary_frame = ttk.LabelFrame(parent, text="Summary", padding=10)
        summary_frame.pack(fill='x', padx=10, pady=(10, 5))

        self.trace_summary_label = ttk.Label(summary_frame,
            text="Items checked: 0  |  Avg time: —  |  Fastest: —  |  Slowest: —",
            font=('Consolas', 10))
        self.trace_summary_label.pack(anchor='w')

        # treeview for per-item trace
        tree_frame = ttk.Frame(parent)
        tree_frame.pack(fill='both', expand=True, padx=10, pady=5)

        columns = ('item', 'result', 'total_ms', 'type_item', 'search_wait', 'qty_check', 'click_add', 'enter_qty', 'clear_input')
        self.trace_tree = ttk.Treeview(tree_frame, columns=columns, show='headings', height=15)

        col_widths = {
            'item': 70, 'result': 160, 'total_ms': 80,
            'type_item': 80, 'search_wait': 90, 'qty_check': 80,
            'click_add': 80, 'enter_qty': 80, 'clear_input': 80
        }
        col_labels = {
            'item': 'Item #', 'result': 'Result', 'total_ms': 'Total (ms)',
            'type_item': 'Type (ms)', 'search_wait': 'Search (ms)', 'qty_check': 'Qty Chk (ms)',
            'click_add': 'Add (ms)', 'enter_qty': 'Enter Qty (ms)', 'clear_input': 'Clear (ms)'
        }
        for col in columns:
            self.trace_tree.heading(col, text=col_labels[col])
            self.trace_tree.column(col, width=col_widths[col], anchor='center')

        scrollbar = ttk.Scrollbar(tree_frame, orient='vertical', command=self.trace_tree.yview)
        scrollbar.pack(side='right', fill='y')
        self.trace_tree.configure(yscrollcommand=scrollbar.set)
        self.trace_tree.pack(side='left', fill='both', expand=True)

        # tag for color-coding rows
        self.trace_tree.tag_configure('skip', foreground='gray')
        self.trace_tree.tag_configure('added', foreground='green')
        self.trace_tree.tag_configure('failed', foreground='red')

        # buttons
        btn_frame = ttk.Frame(parent)
        btn_frame.pack(fill='x', padx=10, pady=5)
        ttk.Button(btn_frame, text="Clear Traces", command=self.clear_traces).pack(side='left', padx=5)
        ttk.Button(btn_frame, text="Export CSV", command=self.export_traces).pack(side='left', padx=5)

        # track how many traces we've displayed
        self._trace_count = 0

    def _poll_traces(self):
        """Poll bot's trace_log and update the Time Trace tab."""
        if self.bot and hasattr(self.bot, 'trace_log'):
            traces = self.bot.trace_log
            while self._trace_count < len(traces):
                t = traces[self._trace_count]
                steps = t.get('steps', {})
                tag = 'skip' if 'SKIP' in t.get('result', '') else 'added' if 'ADDED' in t.get('result', '') else 'failed'
                self.trace_tree.insert('', 'end', values=(
                    t['item'],
                    t['result'],
                    t['total_ms'],
                    steps.get('type_item', '—'),
                    steps.get('search_wait', '—'),
                    steps.get('qty_check', '—'),
                    steps.get('click_add', '—'),
                    steps.get('enter_qty', '—'),
                    steps.get('clear_input', '—'),
                ), tags=(tag,))
                self.trace_tree.see(self.trace_tree.get_children()[-1])
                self._trace_count += 1

            # update summary
            if traces:
                times = [t['total_ms'] for t in traces]
                avg = sum(times) // len(times)
                fastest = min(times)
                slowest = max(times)
                skipped = sum(1 for t in traces if 'SKIP' in t.get('result', ''))
                added = sum(1 for t in traces if 'ADDED' in t.get('result', ''))
                self.trace_summary_label.config(
                    text=f"Items checked: {len(traces)}  |  Added: {added}  |  Skipped: {skipped}  |  "
                         f"Avg: {avg}ms  |  Fastest: {fastest}ms  |  Slowest: {slowest}ms")

        if self.bot_running:
            self.root.after(500, self._poll_traces)

    def clear_traces(self):
        for item in self.trace_tree.get_children():
            self.trace_tree.delete(item)
        self._trace_count = 0
        if self.bot and hasattr(self.bot, 'trace_log'):
            self.bot.trace_log.clear()
        self.trace_summary_label.config(
            text="Items checked: 0  |  Avg time: —  |  Fastest: —  |  Slowest: —")

    def export_traces(self):
        if not self.bot or not hasattr(self.bot, 'trace_log') or not self.bot.trace_log:
            messagebox.showinfo("Export", "No trace data to export.")
            return
        filename = f"trace_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        with open(filename, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['item', 'result', 'total_ms', 'type_item', 'search_wait', 'qty_check', 'click_add', 'enter_qty', 'clear_input'])
            for t in self.bot.trace_log:
                s = t.get('steps', {})
                writer.writerow([t['item'], t['result'], t['total_ms'],
                    s.get('type_item', ''), s.get('search_wait', ''), s.get('qty_check', ''),
                    s.get('click_add', ''), s.get('enter_qty', ''), s.get('clear_input', '')])
        messagebox.showinfo("Export", f"Traces exported to {filename}")

    def create_log_tab(self, parent):
        # log text area
        self.log_text = scrolledtext.ScrolledText(parent, state='disabled', 
                                                  wrap='word', height=20)
        self.log_text.pack(fill='both', expand=True, padx=10, pady=10)
        
        # clear log button
        ttk.Button(parent, text="Clear Logs", 
                  command=self.clear_logs).pack(pady=5)
        
        # set up logging handler
        log_handler = GuiLogHandler(self.log_text)
        log_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', 
                                                  datefmt='%H:%M:%S'))
        logging.getLogger().addHandler(log_handler)
        logging.getLogger().setLevel(logging.INFO)
        
    def load_env_settings(self):
        # load from .env file if exists
        if Path('.env').exists():
            load_dotenv()
            self.username_entry.insert(0, os.getenv('SITE_USERNAME', ''))
            self.password_entry.insert(0, os.getenv('SITE_PASSWORD', ''))
            self.url_entry.delete(0, tk.END)
            self.url_entry.insert(0, os.getenv('SITE_URL', 'https://tap.dor.ms.gov/'))
            
    def save_settings(self):
        # save settings to .env file
        with open('.env', 'w') as f:
            f.write(f"SITE_USERNAME={self.username_entry.get()}\n")
            f.write(f"SITE_PASSWORD={self.password_entry.get()}\n")
            f.write(f"SITE_URL={self.url_entry.get()}\n")
        
        # reload environment variables
        load_dotenv(override=True)
        messagebox.showinfo("Success", "Settings saved successfully!")
        
    def check_csv_file(self):
        # check if orders.csv exists and load it
        if Path('orders.csv').exists():
            self.load_csv()
        
    def load_csv(self):
        # clear existing items
        for item in self.csv_tree.get_children():
            self.csv_tree.delete(item)
        
        # load from csv
        if Path('orders.csv').exists():
            with open('orders.csv', 'r', newline='') as file:
                reader = csv.DictReader(file)
                for row in reader:
                    self.csv_tree.insert('', 'end', values=(
                        row.get('item_number', ''),
                        row.get('quantity', ''),
                        row.get('order_filled', '')
                    ))
        
        self.update_stats()
        
    def save_csv(self):
        # save treeview data to csv
        with open('orders.csv', 'w', newline='') as file:
            fieldnames = ['item_number', 'quantity', 'order_filled']
            writer = csv.DictWriter(file, fieldnames=fieldnames)
            writer.writeheader()
            
            for item in self.csv_tree.get_children():
                values = self.csv_tree.item(item)['values']
                writer.writerow({
                    'item_number': values[0],
                    'quantity': values[1],
                    'order_filled': values[2]
                })
        
        messagebox.showinfo("Success", "CSV saved successfully!")
        
    def add_item(self):
        # add item from entry fields
        item_num = self.item_entry.get()
        quantity = self.quantity_entry.get()
        
        if item_num and quantity:
            self.csv_tree.insert('', 'end', values=(item_num, quantity, ''))
            self.item_entry.delete(0, tk.END)
            self.quantity_entry.delete(0, tk.END)
            self.save_csv()
            self.update_stats()
        
    def add_csv_row(self):
        # add empty row
        self.csv_tree.insert('', 'end', values=('', '', ''))
        
    def delete_csv_row(self):
        # delete selected row
        selected = self.csv_tree.selection()
        if selected:
            self.csv_tree.delete(selected)
            self.save_csv()
            self.update_stats()
            
    def clear_all_orders(self):
        # confirm before clearing everything
        if not messagebox.askyesno("Confirm", "Delete ALL orders from the list?"):
            return
        for item in self.csv_tree.get_children():
            self.csv_tree.delete(item)
        self.save_csv()
        self.update_stats()
        messagebox.showinfo("Success", "All orders cleared!")

    def clear_yes_marks(self):
        # clear all 'yes' marks in order_filled column
        for item in self.csv_tree.get_children():
            values = list(self.csv_tree.item(item)['values'])
            values[2] = ''  # clear order_filled
            self.csv_tree.item(item, values=values)
        self.save_csv()
        self.update_stats()
        messagebox.showinfo("Success", "All 'yes' marks cleared!")
        
    def update_stats(self):
        # update statistics
        total = len(self.csv_tree.get_children())
        filled = sum(1 for item in self.csv_tree.get_children() 
                    if str(self.csv_tree.item(item)['values'][2]).lower() == 'yes')
        remaining = total - filled
        
        self.stats_label.config(text=f"Items Total: {total}\nItems Processed: {filled}\nItems Remaining: {remaining}")
        
    def toggle_bot(self):
        if not self.bot_running:
            self.start_bot()
        else:
            self.stop_bot()
            
    def start_bot(self):
        # validate settings
        if not self.username_entry.get() or not self.password_entry.get():
            messagebox.showerror("Error", "Please enter username and password in Settings tab!")
            return
        
        # save current csv
        self.save_csv()
        
        # update UI
        self.bot_running = True
        self.start_button.config(text="Stop Bot")
        self.status_label.config(text="Status: Running", foreground='green')
        self.progress.start()
        
        # clear stop event
        self.stop_event.clear()
        
        # start trace polling
        self._trace_count = 0
        self._poll_traces()

        # start bot in separate thread
        self.bot_thread = threading.Thread(target=self.run_bot_thread, daemon=True)
        self.bot_thread.start()
        
    def stop_bot(self):
        # set stop event
        self.stop_event.set()
        
        # update UI
        self.bot_running = False
        self.start_button.config(text="Start Bot")
        self.status_label.config(text="Status: Stopped", foreground='red')
        self.progress.stop()
        
        logging.info("Bot stopped by user")
        
    def run_bot_thread(self):
        # create new event loop for this thread
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            loop.run_until_complete(self.run_bot())
        except Exception as e:
            logging.error(f"Bot error: {e}")
        finally:
            loop.close()
            
            # update UI on main thread
            self.root.after(0, self.on_bot_stopped)
            
    def _reset_backorders(self):
        """Reset backorder items to pending so they retry on next start."""
        try:
            from bot_script import read_csv_file, update_csv_file
            items = read_csv_file('orders.csv')
            changed = False
            for item in items:
                if item.get('order_filled', '').lower() == 'backorder':
                    item['order_filled'] = ''
                    changed = True
            if changed:
                update_csv_file('orders.csv', items)
                logging.info("Reset backorder items to pending")
        except Exception as e:
            logging.error(f"Failed to reset backorders: {e}")

    def on_bot_stopped(self):
        # called when bot stops
        self.bot_running = False
        self.bot = None
        self.start_button.config(text="Start Bot")
        self.status_label.config(text="Status: Stopped", foreground='red')
        self.progress.stop()
        # Reset backorder items to pending so they retry on next start
        self._reset_backorders()
        self.load_csv()  # reload csv to show updates
        self.update_stats()
        
    def skip_cooldown(self):
        """User clicked Skip Cooldown — resume immediately."""
        self._skip_cooldown = True
        logging.info("Cooldown skipped by user")

    async def _do_cooldown(self):
        """Run a cooldown break. Returns early if user clicks Skip Cooldown."""
        import random
        try:
            rest_min = float(self.rest_min_entry.get())
            rest_max = float(self.rest_max_entry.get())
        except ValueError:
            rest_min, rest_max = 2.0, 3.0

        rest_seconds = random.uniform(rest_min * 60, rest_max * 60)
        rest_end = time.time() + rest_seconds
        self._skip_cooldown = False

        logging.info(f"Cooldown break: pausing for {rest_seconds/60:.1f} min to avoid detection...")
        self.root.after(0, lambda: self.status_label.config(
            text=f"Status: Cooling down ({rest_seconds/60:.1f} min)", foreground='orange'))
        self.root.after(0, lambda: self.skip_cooldown_button.config(state='normal'))

        while time.time() < rest_end and not self.stop_event.is_set() and not self._skip_cooldown:
            remaining = int(rest_end - time.time())
            mins, secs = divmod(remaining, 60)
            self.root.after(0, lambda m=mins, s=secs: self.status_label.config(
                text=f"Status: Cooldown {m}:{s:02d} remaining (click Skip to resume)", foreground='orange'))
            await asyncio.sleep(1)

        if self._skip_cooldown:
            logging.info("Cooldown skipped — resuming immediately")
        else:
            logging.info("Cooldown complete — resuming operations")

        self.root.after(0, lambda: self.status_label.config(
            text="Status: Running", foreground='green'))
        self.root.after(0, lambda: self.skip_cooldown_button.config(state='disabled'))

    async def run_bot(self):
        # import bot class
        from bot_script import WebAutomationBot, read_csv_file, update_csv_file  # noqa: F401 - used in run_bot
        import random

        headless = self.headless_var.get()
        bot = WebAutomationBot(headless=headless)
        self.bot = bot

        # cooldown tracking
        try:
            work_interval = float(self.work_interval_entry.get()) * 60
        except ValueError:
            work_interval = 15 * 60
        cooldown_enabled = self.cooldown_enabled_var.get()
        last_cooldown = time.time()

        try:
            await bot.setup(use_saved_auth=self.use_saved_auth_var.get())
            logging.info("Bot ready. If prompted for OTP, complete it in the browser.")

            while not self.stop_event.is_set():
                # Check if cooldown is due
                if cooldown_enabled and (time.time() - last_cooldown) >= work_interval:
                    await self._do_cooldown()
                    last_cooldown = time.time()
                    if self.stop_event.is_set():
                        break

                try:
                    # read items from csv
                    items = read_csv_file('orders.csv')

                    # filter unfilled items
                    unfilled_items = [item for item in items
                                    if item.get('order_filled', '').lower() != 'yes']

                    if not unfilled_items:
                        logging.info("All items completed! Checking for new items in 5 seconds...")
                        await asyncio.sleep(5)
                        self.root.after(0, self.load_csv)
                        self.root.after(0, self.update_stats)
                        continue

                    logging.info(f"Checking {len(unfilled_items)} items for availability...")

                    # Start a new order if not already on the order page
                    current_url = bot.page.url
                    if 'itemEntry' not in current_url:
                        await bot.start_order()

                    items_found, total_qty_added = await bot.check_and_process_items(items)

                    if items_found and total_qty_added >= 10:
                        item_numbers = [str(item['item_number']) for item in items_found]
                        logging.info(f"Found {len(items_found)} items, {total_qty_added} total qty: {', '.join(item_numbers)}")
                        await bot.submit_order()
                        update_csv_file('orders.csv', items)
                        remaining = [i for i in items if i.get('order_filled', '').lower() not in ('yes',)]
                        if not remaining:
                            logging.info("All items filled!")
                        else:
                            logging.info(f"{len(remaining)} items remaining — restarting with manual entry...")
                            await asyncio.sleep(2)
                            await bot.start_order()
                    elif items_found and total_qty_added < 10:
                        # Keep items in cart — don't revert. Wait for more to become available.
                        logging.warning(f"Have {total_qty_added} qty in cart (need 10 min). Keeping cart, re-checking in 30s...")
                        await asyncio.sleep(30)
                    else:
                        logging.info("No items available. Checking again in 1 second...")
                        await asyncio.sleep(1)
                    
                    # update UI
                    self.root.after(0, self.load_csv)
                    self.root.after(0, self.update_stats)
                
                except Exception as e:
                    logging.error(f"Error during bot operation: {e}", exc_info=True)
                    
                    # Re-initialize the bot to recover from crash
                    logging.info("Attempting to recover by re-initializing the bot...")
                    try:
                        await bot.cleanup()
                    except Exception:
                        pass
                    bot = WebAutomationBot(headless=headless)
                    self.bot = bot
                    try:
                        await bot.setup(use_saved_auth=True)
                        logging.info("Bot recovered successfully")
                    except Exception as setup_err:
                        logging.error(f"Recovery failed: {setup_err}")
                        await asyncio.sleep(5)
                
        except Exception as e:
            logging.error(f"Bot startup error: {e}", exc_info=True)
        finally:
            try:
                await bot.cleanup()
            except Exception:
                pass
            
    def clear_logs(self):
        self.log_text.configure(state='normal')
        self.log_text.delete(1.0, tk.END)
        self.log_text.configure(state='disabled')

def main():
    root = tk.Tk()
    app = BotGUI(root)
    root.mainloop()

if __name__ == "__main__":
    main()