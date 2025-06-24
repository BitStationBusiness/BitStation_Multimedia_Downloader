import sys
import os
import time
import subprocess
import shutil
import psutil

def main():
    """
    Este script se encarga de reemplazar los archivos de la aplicación,
    actualizar el archivo de versión y reiniciarla.
    """
    if len(sys.argv) < 3:
        print("Error: No se proporcionó el PID del proceso principal y la nueva versión.")
        time.sleep(5)
        return

    try:
        pid = int(sys.argv[1])
        new_version = sys.argv[2]
        
        print(f"Actualizador iniciado. Nueva versión a instalar: {new_version}")
        print(f"Esperando a que el proceso principal (PID: {pid}) se cierre...")

        # Esperar a que el proceso principal termine
        try:
            if psutil.pid_exists(pid):
                parent_process = psutil.Process(pid)
                parent_process.wait(timeout=10)
        except psutil.NoSuchProcess:
            print("El proceso principal ya se ha cerrado.")
        except Exception as e:
            print(f"No se pudo confirmar el cierre del proceso principal: {e}. Continuando de todas formas.")

        time.sleep(2)  # Dar un par de segundos extra para que se liberen los archivos

        print("Proceso principal cerrado. Reemplazando archivos...")
        source_dir = "update_temp"
        target_dir = os.getcwd()

        if not os.path.isdir(source_dir):
            print(f"Error: El directorio de actualización '{source_dir}' no existe.")
            time.sleep(5)
            return

        # Mover los archivos nuevos desde la carpeta temporal a la raíz
        for filename in os.listdir(source_dir):
            source_path = os.path.join(source_dir, filename)
            target_path = os.path.join(target_dir, filename)
            
            # No queremos que el updater se sobreescriba a sí mismo mientras se ejecuta
            if filename.lower() == 'updater.py':
                continue
                
            try:
                if os.path.exists(target_path):
                    os.remove(target_path)
                shutil.move(source_path, target_path)
                print(f" - '{filename}' actualizado.")
            except Exception as e:
                print(f"  -> Error al reemplazar '{filename}': {e}")
        
        # --- PASO CLAVE: Escribir la nueva versión en el archivo ---
        print(f"Actualizando el archivo de versión a '{new_version}'...")
        with open("version.txt", "w") as f:
            f.write(new_version)
        print("Archivo de versión actualizado.")

        print("\nArchivos actualizados. Reiniciando la aplicación...")
        
        try:
            shutil.rmtree(source_dir)
        except Exception as e:
            print(f"No se pudo eliminar la carpeta temporal: {e}")

        # Volver a lanzar la aplicación a través del instalador
        subprocess.Popen(["install.bat"])
        print("Señal de reinicio enviada. El actualizador se cerrará ahora.")

    except Exception as e:
        print(f"Ha ocurrido un error fatal en el actualizador: {e}")
        time.sleep(15)

if __name__ == '__main__':
    main()
