import sys
import re
import subprocess
import os
import yt_dlp
import imageio_ffmpeg
import ctypes  # <-- 1. Importación necesaria para la API de Windows

from PyQt6.QtCore import Qt, QSize, QThread, QObject, pyqtSignal, QSettings
from PyQt6.QtGui import QIcon, QFont
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QLineEdit, QDialog, QFrame, QTableWidget,
    QTableWidgetItem, QHeaderView, QCheckBox, QComboBox, QStyle,
    QProgressBar, QFileDialog
)

URL_REGEX = r'https?://[^\s/$.?#].[^\s]*'

# --- FUNCIÓN PARA OBTENER DATOS REALES DEL SISTEMA ---
def get_system_info():
    info = {"cpu": "No detectado", "ram": "No detectada", "gpu": "No detectada", "cuda": "None"}
    creation_flags = subprocess.CREATE_NO_WINDOW
    try:
        cpu_cmd = ['powershell', '-NoProfile', '-Command', "Get-CimInstance -ClassName Win32_Processor | Select-Object -ExpandProperty Name"]
        cpu_result = subprocess.run(cpu_cmd, capture_output=True, text=True, creationflags=creation_flags, check=True, encoding='utf-8')
        info["cpu"] = cpu_result.stdout.strip()

        ram_cmd = ['powershell', '-NoProfile', '-Command', "$mem = Get-CimInstance -ClassName Win32_ComputerSystem; [math]::Round($mem.TotalPhysicalMemory / 1GB, 0)"]
        ram_result = subprocess.run(ram_cmd, capture_output=True, text=True, creationflags=creation_flags, check=True, encoding='utf-8')
        info["ram"] = f"{ram_result.stdout.strip()} GB"

        nvidia_mem_gb = None
        try:
            nvidia_smi_cmd = ['nvidia-smi', '--query-gpu=memory.total', '--format=csv,noheader,nounits']
            nvidia_result = subprocess.run(nvidia_smi_cmd, capture_output=True, text=True, creationflags=creation_flags, check=True, encoding='utf-8')
            nvidia_mem_mib = int(nvidia_result.stdout.strip().splitlines()[0])
            nvidia_mem_gb = round(nvidia_mem_mib / 1024)
        except Exception:
            nvidia_mem_gb = None
            
        gpu_list = []
        ps_command = "Get-CimInstance -ClassName Win32_VideoController | ForEach-Object { $_.Name + '|||' + $_.AdapterRAM }"
        ps_result = subprocess.run(['powershell', '-NoProfile', '-Command', ps_command], capture_output=True, text=True, creationflags=creation_flags, check=True, encoding='utf-8')
        
        nvidia_processed = False
        for line in ps_result.stdout.strip().splitlines():
            if '|||' not in line: continue
            
            name, ram_bytes_str = line.split('|||', 1)
            name = name.strip()

            if 'Microsoft' in name or 'Virtual' in name or 'MrIdd' in name: continue

            if 'NVIDIA' in name and nvidia_mem_gb is not None and not nvidia_processed:
                gpu_list.append(f"{name} ({nvidia_mem_gb}GB)")
                nvidia_processed = True
            else:
                try:
                    ram_bytes = int(ram_bytes_str)
                    if ram_bytes > 0:
                        ram_mb = ram_bytes / (1024 * 1024)
                        if ram_mb >= 1000:
                            ram_gb = round(ram_mb / 1024)
                            gpu_list.append(f"{name} ({ram_gb}GB)")
                        else:
                            gpu_list.append(f"{name} ({int(ram_mb)}MB)")
                    else:
                        gpu_list.append(name)
                except (ValueError, TypeError):
                    gpu_list.append(name)

        info['gpu'] = "\n".join(gpu_list) if gpu_list else "No detectada"

        try:
            cuda_cmd = ['nvidia-smi']
            cuda_result = subprocess.run(cuda_cmd, capture_output=True, text=True, creationflags=creation_flags, check=True, encoding='utf-8')
            for line in cuda_result.stdout.splitlines():
                if "CUDA Version" in line:
                    version_part = line.split("CUDA Version:")[1]
                    info["cuda"] = version_part.split("|")[0].strip()
                    break
        except (FileNotFoundError, subprocess.CalledProcessError):
            info["cuda"] = "None"
    except Exception as e:
        print(f"Error al obtener la información del sistema: {e}")
    return info

# --- Ventana de Configuración ---
class SettingsWindow(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.main_window = parent
        self.setWindowTitle("Configuración")
        self.setWindowIcon(QIcon("BitStation.ico"))
        self.setFixedSize(450, 600)
        self.setModal(True) 
        main_layout = QVBoxLayout(self); main_layout.setContentsMargins(15, 10, 15, 15); main_layout.setSpacing(10)
        top_bar_layout = QHBoxLayout(); back_button = QPushButton("←"); font_arrow = QFont(); font_arrow.setPointSize(16); font_arrow.setBold(True)
        back_button.setFont(font_arrow); back_button.setFixedSize(QSize(40, 40)); back_button.clicked.connect(self.close)
        title_label = QLabel("Configuración"); font_title = QFont(); font_title.setPointSize(14); title_label.setFont(font_title)
        top_bar_layout.addWidget(back_button); top_bar_layout.addWidget(title_label); top_bar_layout.addStretch()
        video_path_label = QLabel("Ruta actual de Videos:"); self.video_path_display = QLineEdit(self.main_window.video_path); self.video_path_display.setReadOnly(True)
        video_folder_button = QPushButton("elegir carpeta de destino"); video_folder_button.clicked.connect(self.select_video_path)
        audio_path_label = QLabel("Ruta Actual de Audios:"); self.audio_path_display = QLineEdit(self.main_window.audio_path); self.audio_path_display.setReadOnly(True)
        audio_folder_button = QPushButton("elegir carpeta de destino"); audio_folder_button.clicked.connect(self.select_audio_path)
        separator = QFrame(); separator.setFrameShape(QFrame.Shape.HLine); separator.setFrameShadow(QFrame.Shadow.Sunken)
        caracteristicas_label = QLabel("Características:"); font_caracteristicas = QFont(); font_caracteristicas.setBold(True); caracteristicas_label.setFont(font_caracteristicas)
        ram_label = QLabel(f"RAM: {self.main_window.system_info.get('ram', 'N/A')}"); procesador_label = QLabel(f"Procesador: {self.main_window.system_info.get('cpu', 'N/A')}")
        gpu_label = QLabel(f"GPU: {self.main_window.system_info.get('gpu', 'N/A')}"); cuda_label = QLabel(f"Versión CUDA: {self.main_window.system_info.get('cuda', 'N/A')}")
        version_label = QLabel("Version: v0.1"); update_button = QPushButton("Update avaible: v0.2")
        main_layout.addLayout(top_bar_layout); main_layout.addSpacing(20); main_layout.addWidget(video_path_label); main_layout.addWidget(self.video_path_display); main_layout.addWidget(video_folder_button)
        main_layout.addSpacing(20); main_layout.addWidget(audio_path_label); main_layout.addWidget(self.audio_path_display); main_layout.addWidget(audio_folder_button)
        main_layout.addSpacing(20); main_layout.addWidget(separator); main_layout.addSpacing(10); main_layout.addWidget(caracteristicas_label); main_layout.addWidget(ram_label)
        main_layout.addWidget(procesador_label); main_layout.addWidget(gpu_label); main_layout.addWidget(cuda_label); main_layout.addSpacing(10); main_layout.addWidget(version_label)
        main_layout.addStretch(); main_layout.addWidget(update_button)

    def select_video_path(self):
        folder = QFileDialog.getExistingDirectory(self, "Seleccionar Carpeta para Videos", self.main_window.video_path)
        if folder: self.main_window.set_video_path(folder); self.video_path_display.setText(folder)

    def select_audio_path(self):
        folder = QFileDialog.getExistingDirectory(self, "Seleccionar Carpeta para Audios", self.main_window.audio_path)
        if folder: self.main_window.set_audio_path(folder); self.audio_path_display.setText(folder)

class FormatFetcherWorker(QObject):
    formats_fetched = pyqtSignal(int, list); error = pyqtSignal(int, str); finished = pyqtSignal()
    def __init__(self, row, url): super().__init__(); self.row = row; self.url = url
    def run(self):
        try:
            with yt_dlp.YoutubeDL({'quiet': True, 'nocolor': True}) as ydl: info_dict = ydl.extract_info(self.url, download=False)
            self.formats_fetched.emit(self.row, info_dict.get('formats', []))
        except Exception as e: self.error.emit(self.row, str(e))
        finally: self.finished.emit()

class DownloadWorker(QObject):
    finished = pyqtSignal(dict, str); progress = pyqtSignal(int, int); error = pyqtSignal(dict, str)
    def __init__(self, job, options): super().__init__(); self.job = job; self.ydl_opts = options; self.is_running = True
    def run(self):
        try:
            self.ydl_opts['progress_hooks'] = [self.progress_hook]
            with yt_dlp.YoutubeDL(self.ydl_opts) as ydl: ydl.download([self.job['url']])
            if self.is_running: self.finished.emit(self.job, "Completado")
        except Exception as e:
            if self.is_running: self.error.emit(self.job, str(e))
    def progress_hook(self, d):
        if d['status'] == 'downloading':
            percent_str = d.get('_percent_str', '0.0%').strip()
            ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
            percent_str = ansi_escape.sub('', percent_str)
            try:
                percent = int(float(percent_str.strip('%'))); self.progress.emit(self.job['row'], percent)
            except (ValueError, TypeError): pass
    def stop(self): self.is_running = False

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = QSettings("BitStation", "MultimediaDownloader")
        self.load_settings()
        self.ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe(); print(f"[INFO] FFMPEG encontrado en: {self.ffmpeg_path}")
        self.download_modes = ['Ambos', 'Audio', 'Video']
        self.current_download_mode_index = 0
        self.is_downloading = False
        self.active_thread = None; self.active_worker = None
        self.download_queue = []
        self.active_format_fetchers = {}
        self.active_downloads = {}
        self.system_info = get_system_info()
        self.setWindowTitle("Descargador Multimedia")
        self.setWindowIcon(QIcon("BitStation.ico"))
        self.setGeometry(100, 100, 900, 600) 
        self.setup_ui(); self.setup_connections()
        
    def setup_ui(self):
        central_widget = QWidget(); self.setCentralWidget(central_widget); main_layout = QVBoxLayout(central_widget)
        top_controls_layout = QHBoxLayout(); self.settings_button = QPushButton("⚙️"); font_settings = QFont(); font_settings.setPointSize(16)
        self.settings_button.setFont(font_settings); self.settings_button.setFixedSize(QSize(40, 40))
        self.prompt_label = QLabel("¿Qué desea descargar?"); self.download_mode_button = QPushButton(self.download_modes[self.current_download_mode_index])
        top_controls_layout.addWidget(self.settings_button); top_controls_layout.addWidget(self.prompt_label); top_controls_layout.addStretch(); top_controls_layout.addWidget(self.download_mode_button)
        search_layout = QHBoxLayout(); self.search_bar = QLineEdit(); self.search_bar.setPlaceholderText("Pega uno o más links aquí (Ctrl+V)..."); self.search_bar.setMinimumHeight(35)
        self.master_download_button = QPushButton(); self.master_download_button.setFixedSize(QSize(40, 35)); self.update_master_download_icon()
        search_layout.addWidget(self.search_bar); search_layout.addWidget(self.master_download_button)
        self.table = QTableWidget(); self.table.setColumnCount(6); self.table.setHorizontalHeaderLabels(['#', 'Link', 'Estado', 'Formato', 'Resolución', 'Eliminar'])
        header = self.table.horizontalHeader(); header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents); header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents); header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents); header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        main_layout.addLayout(top_controls_layout); main_layout.addLayout(search_layout); main_layout.addWidget(self.table)

    def setup_connections(self):
        self.search_bar.textChanged.connect(self.handle_paste); self.download_mode_button.clicked.connect(self.toggle_download_mode)
        self.master_download_button.clicked.connect(self.toggle_master_download); self.settings_button.clicked.connect(self.open_settings_window)

    def load_settings(self):
        default_video_path = "C:\\BitStation\\BitStation_Multimedia_Downloader\\Video"; default_audio_path = "C:\\BitStation\\BitStation_Multimedia_Downloader\\Audio"
        self.video_path = self.settings.value("videoPath", defaultValue=default_video_path); self.audio_path = self.settings.value("audioPath", defaultValue=default_audio_path)
        os.makedirs(self.video_path, exist_ok=True); os.makedirs(self.audio_path, exist_ok=True)
        
    def set_video_path(self, path): self.video_path = path; self.settings.setValue("videoPath", path)
    def set_audio_path(self, path): self.audio_path = path; self.settings.setValue("audioPath", path)
    def open_settings_window(self): dialog = SettingsWindow(self); dialog.exec()

    def handle_paste(self, text):
        urls = re.findall(URL_REGEX, text)
        if urls:
            for url in urls: self.add_link_to_table(url)
            self.search_bar.blockSignals(True); self.search_bar.clear(); self.search_bar.blockSignals(False)

    def add_link_to_table(self, link_text):
        row_position = self.table.rowCount(); self.table.insertRow(row_position)
        item_num = QTableWidgetItem(str(row_position + 1)); item_num.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.table.setItem(row_position, 0, item_num)
        self.table.setItem(row_position, 1, QTableWidgetItem(link_text))
        progress_bar = QProgressBar(); progress_bar.setFormat("En cola"); self.table.setCellWidget(row_position, 2, progress_bar)
        self.table.setCellWidget(row_position, 3, self.create_format_widget())
        combo = QComboBox(); combo.addItem("Cargando..."); combo.setEnabled(False); self.table.setCellWidget(row_position, 4, combo)
        self.table.setCellWidget(row_position, 5, self.create_delete_widget())
        self.fetch_formats_for_row(row_position, link_text)

    def fetch_formats_for_row(self, row, url):
        thread = QThread(self); worker = FormatFetcherWorker(row, url); worker.moveToThread(thread)
        self.active_format_fetchers[row] = (thread, worker)
        thread.started.connect(worker.run); worker.formats_fetched.connect(self.on_formats_fetched); worker.error.connect(self.on_formats_error)
        worker.finished.connect(thread.quit); worker.finished.connect(worker.deleteLater); thread.finished.connect(thread.deleteLater); thread.finished.connect(lambda r=row: self.cleanup_format_fetcher(r))
        thread.start()

    def cleanup_format_fetcher(self, row):
        if row in self.active_format_fetchers: del self.active_format_fetchers[row]

    def on_formats_fetched(self, row, formats):
        combo = self.table.cellWidget(row, 4)
        if not combo: return
        combo.clear(); combo.setEnabled(True)
        combo.addItem("Mejor Calidad", "bestvideo+bestaudio/best")
        added_formats = set()
        for f in sorted(formats, key=lambda x: (x.get('height') or 0, x.get('acodec') is not None), reverse=True):
            vcodec = f.get('vcodec'); height = f.get('height')
            if vcodec and vcodec != 'none' and height:
                if height in added_formats: continue
                added_formats.add(height)
                ext = f.get('ext', 'N/A')
                filesize_approx = f.get('filesize_approx'); filesize_str = f"~{filesize_approx / (1024*1024):.1f}MB" if filesize_approx else ""
                display_text = f"{height}p ({ext}) {filesize_str}".strip(); format_id = f.get('format_id')
                if format_id: combo.addItem(display_text, format_id)

    def on_formats_error(self, row, error_message):
        combo = self.table.cellWidget(row, 4)
        if combo: combo.clear(); combo.addItem("Error")
        print(f"Error al obtener formatos para la fila {row+1}: {error_message}")

    def create_format_widget(self):
        widget = QWidget(); layout = QHBoxLayout(widget); layout.setContentsMargins(5, 0, 5, 0)
        cb_audio = QCheckBox("Audio"); cb_video = QCheckBox("Video")
        mode = self.download_modes[self.current_download_mode_index]
        if mode == 'Audio': cb_audio.setChecked(True); cb_video.setChecked(False)
        elif mode == 'Video' or mode == 'Ambos': cb_audio.setChecked(True); cb_video.setChecked(True)
        layout.addWidget(cb_audio); layout.addWidget(cb_video)
        return widget

    def create_delete_widget(self):
        widget = QWidget(); layout = QHBoxLayout(widget); layout.setContentsMargins(0, 0, 0, 0); layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        delete_button = QPushButton("❌"); delete_button.setFixedSize(QSize(28, 28)); delete_button.clicked.connect(self.delete_row)
        layout.addWidget(delete_button)
        return widget
        
    def delete_row(self):
        button = self.sender()
        if button:
            parent_widget = button.parent()
            if parent_widget:
                row = self.table.indexAt(parent_widget.pos()).row()
                if row >= 0:
                    if row in self.active_downloads: thread, worker = self.active_downloads.pop(row); worker.stop(); thread.quit(); thread.wait()
                    if row in self.active_format_fetchers: thread, worker = self.active_format_fetchers.pop(row); worker.finished.disconnect(); thread.quit(); thread.wait()
                    self.table.removeRow(row); self.update_row_numbers()

    def update_row_numbers(self):
        for row in range(self.table.rowCount()): self.table.item(row, 0).setText(str(row + 1))

    def toggle_download_mode(self):
        self.current_download_mode_index = (self.current_download_mode_index + 1) % len(self.download_modes)
        self.download_mode_button.setText(self.download_modes[self.current_download_mode_index])
        
    def toggle_master_download(self):
        if self.is_downloading:
            self.is_downloading = False; self.update_master_download_icon()
            if self.active_thread and self.active_worker: self.active_worker.stop()
        else:
            self.build_download_queue()
            if self.download_queue: self.is_downloading = True; self.update_master_download_icon(); self.start_next_download()
        
    def update_master_download_icon(self):
        style = self.style();
        if self.is_downloading: icon = style.standardIcon(QStyle.StandardPixmap.SP_MediaPause)
        else: icon = style.standardIcon(QStyle.StandardPixmap.SP_ArrowDown)
        self.master_download_button.setIcon(icon)

    def build_download_queue(self):
        self.download_queue.clear()
        for row in range(self.table.rowCount()):
            progress_bar = self.table.cellWidget(row, 2)
            if progress_bar and progress_bar.format() == "En cola":
                format_widget = self.table.cellWidget(row, 3); checkboxes = format_widget.findChildren(QCheckBox)
                download_audio = checkboxes[0].isChecked(); download_video = checkboxes[1].isChecked()
                url = self.table.item(row, 1).text(); combo_res = self.table.cellWidget(row, 4); format_id = combo_res.currentData()
                if download_video: self.download_queue.append({'row': row, 'type': 'video', 'url': url, 'format_id': format_id})
                if download_audio: self.download_queue.append({'row': row, 'type': 'audio', 'url': url, 'format_id': format_id})

    def start_next_download(self):
        if not self.is_downloading or not self.download_queue:
            self.is_downloading = False; self.update_master_download_icon(); return
        
        job = self.download_queue.pop(0)
        self.start_download_for_job(job)

    def start_download_for_job(self, job):
        row, url, job_type, format_id = job['row'], job['url'], job['type'], job['format_id']
        progress_bar = self.table.cellWidget(row, 2)
        if isinstance(progress_bar, QProgressBar): progress_bar.setFormat(f"Descargando {job_type}... %p%")

        path = self.video_path; format_selection = None
        
        if job_type == 'video':
            video_format = format_id if "best" not in format_id else "bestvideo"
            format_selection = f"{video_format}+bestaudio/best"
            path = self.video_path
        elif job_type == 'audio':
            format_selection = 'bestaudio/best'
            path = self.audio_path
        
        if not format_selection: self.on_download_error(job, "Formato inválido"); return

        ydl_opts = {'format': format_selection, 'outtmpl': os.path.join(path, '%(title)s.%(ext)s'), 'ffmpeg_location': self.ffmpeg_path, 'nocolor': True}
        
        self.active_thread = QThread(self); self.active_worker = DownloadWorker(job, ydl_opts); self.active_worker.moveToThread(self.active_thread)
        self.active_thread.started.connect(self.active_worker.run); self.active_worker.finished.connect(self.on_download_finished)
        self.active_worker.error.connect(self.on_download_error); self.active_worker.progress.connect(self.update_download_progress)
        self.active_thread.finished.connect(self.active_thread.deleteLater); self.active_worker.finished.connect(self.active_worker.deleteLater)
        self.active_thread.start()

    def update_download_progress(self, row, percent):
        widget = self.table.cellWidget(row, 2)
        if isinstance(widget, QProgressBar): widget.setValue(percent)

    def on_download_finished(self, job, message):
        row = job['row']; is_last_job_for_row = not any(q_job['row'] == row for q_job in self.download_queue)
        if is_last_job_for_row:
            widget = self.table.cellWidget(row, 2)
            if isinstance(widget, QProgressBar): widget.setValue(100); widget.setFormat(message)
        self.active_thread = None; self.active_worker = None
        self.start_next_download()
    
    def on_download_error(self, job, error_message):
        row = job['row']; widget = self.table.cellWidget(row, 2)
        if isinstance(widget, QProgressBar): widget.setFormat("Error")
        print(f"Error en la fila {row+1} (trabajo: {job['type']}): {error_message}")
        self.download_queue = [q_job for q_job in self.download_queue if q_job['row'] != row]
        self.active_thread = None; self.active_worker = None
        self.start_next_download()

    def closeEvent(self, event):
        self.is_downloading = False
        if self.active_worker: self.active_worker.stop()
        if self.active_thread: self.active_thread.quit(); self.active_thread.wait()
        for row, (thread, worker) in list(self.active_format_fetchers.items()):
            thread.quit(); thread.wait()
        event.accept()
        
if __name__ == "__main__":
    # --- CÓDIGO PARA EL ICONO DE LA BARRA DE TAREAS DE WINDOWS ---
    # Debe ejecutarse ANTES de crear QApplication
    if sys.platform == 'win32':
        myappid = 'BitStation.MultimediaDownloader.1.0' 
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
    
    app = QApplication(sys.argv)
    # Establecer el icono para la aplicación globalmente
    app.setWindowIcon(QIcon("BitStation.ico"))
    
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
