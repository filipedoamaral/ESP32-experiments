import tkinter as tk
import threading
import asyncio
import re
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from collections import deque
from bleak import BleakScanner, BleakClient

plt.rcParams.update({'font.size': 8})
# (Opcional) Ajustar o tamanho da fonte da legenda se necessário
plt.rcParams['legend.fontsize'] = 7

# --- Variáveis Globais ---
SERVICE_UUID = "00001800-0000-1000-8000-00805f9b34fb"
READ_CHARACTERISTIC_UUID = "00001801-0000-1000-8000-00805f9b34fb" # Corrigido: UUID de leitura
WRITE_CHARACTERISTIC_UUID = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E" # Corrigido: UUID de escrita
Time = 0
TARGET_ADDRESS = "E8:06:90:66:C0:B2"

# Armazena os valores do sensor (limitado aos últimos 50 pontos)
MAX_POINTS = 50 
data_bpm = deque([0] * MAX_POINTS, maxlen=MAX_POINTS)
data_oxy = deque([0] * MAX_POINTS, maxlen=MAX_POINTS)

time_stamps = deque(range(MAX_POINTS), maxlen=MAX_POINTS) # Eixo X simples (tempo)

last_bpm = 0.0
last_oxy = 0.0

# --- Classe de Gerenciamento BLE ---
class BLEManager:
    """Gerencia o estado da conexão BLE para evitar race conditions."""
    def __init__(self):
        self.client = None
        self.is_connecting = False

    async def connect(self, address, connect_callback, disconnect_callback):
        if self.client or self.is_connecting:
            print("Conexão já em andamento ou estabelecida.")
            return

        self.is_connecting = True
        try:
            self.client = BleakClient(address, disconnected_callback=disconnect_callback)
            await self.client.connect(timeout=15.0)
            if self.client.is_connected:
                await self.client.start_notify(READ_CHARACTERISTIC_UUID, notification_handler)
                root.after(0, connect_callback, address)
        except Exception as e:
            self.client = None
            raise e
        finally:
            self.is_connecting = False

    async def disconnect(self):
        if self.client and self.client.is_connected:
            try:
                await self.client.stop_notify(READ_CHARACTERISTIC_UUID)
                await self.client.disconnect()
            except Exception as e:
                print(f"Erro durante a desconexão: {e}")
        self.client = None

    async def write(self, uuid, data):
        if self.client and self.client.is_connected:
            await self.client.write_gatt_char(uuid, data)

ble_manager = BLEManager()

# --- Lógica de Comunicação (Bleak) ---

def notification_handler(sender, data):
    """Função chamada quando uma notificação BLE com MÚLTIPLOS dados é recebida."""
    global last_bpm, last_oxy

    try:
        received_string = data.decode('utf-8').strip()
        
        # Exemplo de string esperada: "T:25.5,B:80,O:98.5"
        
        # Use REGEX para extrair os números após T:, B:, e O:
        bpm_match = re.search(r'B:([\d\.\-]+)', received_string)
        oxy_match = re.search(r'O:([\d\.\-]+)', received_string)

        if bpm_match and oxy_match:
            # 1. Converte os valores
            bpm_value = float(bpm_match.group(1))
            oxy_value = float(oxy_match.group(1))

            # 2. Atualiza os dados para o gráfico
            data_bpm.append(bpm_value)
            data_oxy.append(oxy_value)
            
            # 3. Armazena os últimos valores
            last_bpm = bpm_value
            last_oxy = oxy_value

            # 4. Chama a atualização da UI na thread principal
            schedule_plot_update()
        else:
            print(f"Dados recebidos em formato inválido: {received_string}")
            
    except ValueError as e:
        print(f"Erro ao converter dados ('{received_string}'): {e}")
    except Exception as e:
        print(f"Erro inesperado no handler: {e}")

async def scan_for_devices(scan_timout):
    """Busca dispositivos BLE e atualiza a lista na UI."""
    # O timeout de 10.0s faz com que o Bleak espere por 10 segundos
    devices = await BleakScanner.discover(timeout=scan_timout) 
    
    # O restante da lógica de preenchimento da lista permanece a mesma...
    for d in devices:
        # Adiciona o dispositivo à lista de forma thread-safe
        if d.name:
            root.after(0, lambda name=d.name, address=d.address: 
                       listbox_devices.insert(tk.END, f"{name} ({address})"))
    
    # Lógica de Finalização (chamada na thread principal após o Bleak terminar) ---
    def finalize_scan():
        btn_scan.config(state=tk.NORMAL)
        global Time
        if Time <= 0:
            status_label.config(text="Busca concluída.")

    root.after(0, finalize_scan)

def on_ble_disconnected(client):
    """Callback chamado pela Bleak quando a conexão é perdida inesperadamente."""
    ble_manager.client = None
    root.after(0, lambda: [
        status_label.config(text="Dispositivo desconectado."),
        reset_ui_to_disconnected_state()
    ])

def on_ble_connected(address):
    """Callback para atualizar a UI quando a conexão é bem-sucedida."""
    status_label.config(text=f"CONECTADO a: {address}")
    btn_connect.config(text="Desconectar", command=desconectar_dispositivo, state=tk.NORMAL)
    btn_send_on.config(state=tk.NORMAL)
    btn_scan.config(state=tk.DISABLED)
    listbox_devices.pack_forget()
    btn_connect_direct.pack_forget()
    frame_buttons.pack_forget()
    btn_send_on.pack(pady=5) # Mostra o botão de envio

async def connect_to_ble(address):
    """Conecta ao endereço BLE fornecido."""
    try:
        await ble_manager.connect(address, on_ble_connected, on_ble_disconnected)
    except Exception as e:
        root.after(0, lambda: [
            status_label.config(text=f"Falha na conexão: {str(e)[:50]}..."),
            btn_connect.config(state=tk.NORMAL)
        ])
        ble_manager.client = None

async def write_to_ble(value_bytes):
    """Escreve um valor na característica BLE."""
    if ble_manager.client and ble_manager.client.is_connected:
        try:
            await ble_manager.write(WRITE_CHARACTERISTIC_UUID, value_bytes)
            root.after(0, lambda: status_label.config(text=f"Comando enviado: {value_bytes.decode()}"))
        except Exception as e:
            root.after(0, lambda: status_label.config(text=f"Erro ao enviar comando: {e}"))
    else:
        root.after(0, lambda: status_label.config(text="Erro: Não conectado ao ESP32."))

update_job = None

def update_plot_ui():
    """
    Atualiza todas as linhas do gráfico e os labels na Thread principal do Tkinter.
    """
    global data_bpm, data_oxy 
    global line_bpm, line_oxy # Inclua se não estiver usando classes

    # 1. Atualiza os dados de CADA linha (CORREÇÃO DO set_ydata)
    line_bpm.set_ydata(data_bpm) 
    line_oxy.set_ydata(data_oxy) 
    
    # 2. Reajusta dinamicamente os limites do eixo Y com base em TODOS os dados
    try:
        # Combina todos os dados para calcular os limites min/max
        all_data = list(data_bpm) + list(data_oxy)
        
        if all_data:
            y_min, y_max = min(all_data), max(all_data)
            
            # Adiciona uma margem de 10%
            margin = (y_max - y_min) * 0.1 
            
            # Ajusta para garantir que há pelo menos um pequeno intervalo se y_min == y_max
            if margin == 0:
                 margin = 1 

            ax.set_ylim(y_min - margin, y_max + margin)
            
        canvas.draw_idle() 

    except Exception as e:
        # Garante que a atualização da UI não trave o programa por causa de um erro no gráfico
        print(f"Erro ao atualizar gráfico: {e}")

    # 3. Atualiza os Labels (mantendo a lógica anterior)
    last_value_label.config(text=
        f"BPM: {last_bpm:.0f} | "
        f"Oxigenação: {last_oxy:.1f}%"
    )

def schedule_plot_update():
    """Agenda a atualização do gráfico para evitar sobrecarga na UI."""
    global update_job
    if update_job:
        root.after_cancel(update_job)
    # Agenda a atualização para ocorrer em 100ms, agrupando várias chamadas rápidas
    update_job = root.after(100, update_plot_ui)

# --- Funções de Threading (Lógica de Execução) ---

def run_async_task(coroutine, *args):
    """Executa uma função assíncrona em uma nova thread."""
    def run_in_thread():
        asyncio.run(coroutine(*args))
    
    thread = threading.Thread(target=run_in_thread, daemon=True)
    thread.start()

# --- Funções Ligadas aos Botões (Tkinter) ---

def update_status_countdown():
    """Atualiza o Label de status com a contagem regressiva."""
    global Time
    if Time > 0:
        status_label.config(text=f"Escaneando... {Time}s restantes")
        Time -= 1
        # Agenda a próxima chamada em 1000ms (1 segundo)
        root.after(1000, update_status_countdown)
    else:
        # Quando a contagem chega a zero, o texto de busca concluída será definido pela run_ble_scan
        pass

def iniciar_busca_ble():
    """Chamado pelo botão 'Buscar'. Inicia a busca BLE e o timer na thread."""
    global Time
    
    # 1. Configura a contagem regressiva
    SCAN_DURATION_SECONDS = 10
    Time = SCAN_DURATION_SECONDS
    
    # Limpa a lista antes de escanear novamente
    listbox_devices.delete(0, tk.END)
    
    # 2. Desativa o botão e inicia o timer da UI
    btn_scan.config(state=tk.DISABLED) 
    update_status_countdown() # Inicia o timer da contagem regressiva
    
    # 3. Inicia o BLE (10.0s de timeout)
    run_async_task(scan_for_devices, float(SCAN_DURATION_SECONDS)) 
    
    # A reativação do botão e a mensagem de "Busca concluída"
    # agora serão feitas APÓS o tempo de 10s ter passado no Bleak (dentro de scan_for_devices)

def conectar_dispositivo_direto():
    """Tenta conectar ao endereço MAC definido no TARGET_ADDRESS, ignorando o scan."""
    
    # 1. Verifica se o endereço MAC foi preenchido
    if TARGET_ADDRESS == "00:00:00:00:00:00":
        status_label.config(text="ERRO: Preencha o TARGET_ADDRESS com o MAC real do ESP32!")
        return
        
    address_to_connect = TARGET_ADDRESS

    # 2. Inicia a conexão na thread separada
    run_async_task(connect_to_ble, address_to_connect)
    status_label.config(text=f"Tentando conexão direta com: {address_to_connect}...")

def conectar_dispositivo():
    """Chamado pelo botão 'Conectar'. Inicia a conexão na thread."""
    # Previne múltiplas tentativas de conexão
    if ble_manager.client and ble_manager.client.is_connected:
        status_label.config(text="Já conectado a um dispositivo!")
        return

    try:
        selected_item = listbox_devices.get(listbox_devices.curselection())
        address = selected_item.split('(')[-1].strip(')')
        
        btn_connect.config(state=tk.DISABLED)
        status_label.config(text=f"Tentando conectar a: {address}...")
        
        run_async_task(connect_to_ble, address)
        
    except tk.TclError:
        status_label.config(text="Erro: Selecione um dispositivo na lista primeiro!")
    except IndexError:
        status_label.config(text="Erro: Lista vazia ou nenhuma seleção.")

def reset_ui_to_disconnected_state():
    """Restaura a UI para o estado desconectado."""
    status_label.config(text="Desconectado.")
    btn_connect.config(text="Conectar", command=conectar_dispositivo, state=tk.NORMAL)
    # O botão de conexão é desativado até que um item da lista seja selecionado
    btn_connect.config(state=tk.DISABLED)
    btn_send_on.config(state=tk.DISABLED)
    btn_scan.config(state=tk.NORMAL)
    last_value_label.config(text="Último Valor: N/A")

    # Reexibe os widgets de busca/conexão
    frame_buttons.pack(pady=10)
    btn_connect_direct.pack(side=tk.LEFT, padx=5)
    btn_send_on.pack_forget() # Esconde o botão de envio
    listbox_devices.pack(padx=10, pady=(0, 10))

def desconectar_dispositivo():
    """Desconecta do dispositivo BLE."""
    async def disconnect():
        try:
            await ble_manager.disconnect()
        except Exception as e:
            print(f"Erro durante desconexão: {e}")
        finally:
            root.after(0, reset_ui_to_disconnected_state)
    
    run_async_task(disconnect)

# --- Configuração da Janela Principal (UI) ---

root = tk.Tk()
root.title("App Controle BLE (ESP32)")

# 1. Rótulo de Status
status_label = tk.Label(root, text="Aperte 'Buscar Dispositivo BLE' para começar.", bd=1, relief=tk.SUNKEN, anchor=tk.W)
status_label.pack(fill=tk.X, pady=(5, 0), padx=5)

# 2. Botões de Ação
frame_buttons = tk.Frame(root)
frame_buttons.pack(pady=10)

btn_scan = tk.Button(frame_buttons, text="1. Buscar Dispositivo BLE", command=iniciar_busca_ble)
btn_scan.pack(side=tk.LEFT, padx=5)

btn_connect = tk.Button(frame_buttons, text="2. Conectar", command=conectar_dispositivo, state=tk.DISABLED)
btn_connect.pack(side=tk.LEFT, padx=5)

btn_connect_direct = tk.Button(root, text="Conexão Direta (MAC)", command=conectar_dispositivo_direto)
btn_connect_direct.pack(side=tk.LEFT, padx=5)

# Botão de envio (inicialmente desativado)
btn_send_on = tk.Button(root, text="Enviar '1'", command=lambda: run_async_task(write_to_ble, b'1'), state=tk.DISABLED)

# 3. Lista de Dispositivos
listbox_devices = tk.Listbox(root, width=60, height=10)
listbox_devices.bind('<<ListboxSelect>>', lambda e: btn_connect.config(state=tk.NORMAL)) # Ativa o botão ao selecionar
listbox_devices.pack(padx=10, pady=(0, 10))

# 1. Configuração do Gráfico Matplotlib ---
fig, ax = plt.subplots(figsize=(6, 3), dpi=100)

# Ajusta as margens para dar espaço ao título e aos rótulos dos eixos.
# 'top=0.80' reserva 20% de espaço no topo para o título.
fig.subplots_adjust(top=0.85, bottom=0.15)

ax.set_title("Monitoramento de Sensores em Tempo Real")
ax.set_ylabel("Valor do Sensor")
ax.set_xlabel("Tempo (últimos 50 pontos)")

# Cria os TRÊS objetos de linha que serão atualizados
# Use as variáveis globais de dados (data_temp, data_bpm, data_oxy)
line_bpm, = ax.plot(time_stamps, data_bpm, label='BPM', color='blue')
line_oxy, = ax.plot(time_stamps, data_oxy, label='Oxigenação (%)', color='green')

# Adiciona a legenda para identificar as linhas
ax.legend(loc='upper left')

# 2. Integra a figura ao Tkinter
canvas = FigureCanvasTkAgg(fig, master=root)
canvas_widget = canvas.get_tk_widget()
canvas_widget.pack(padx=10, pady=10)

# 3. Label para mostrar o último valor numérico lido
last_value_label = tk.Label(root, text="Último Valor: N/A", font=("Arial", 12, "bold"))
last_value_label.pack(pady=5)

def on_closing():
    """Função chamada quando a janela é fechada."""
    if ble_manager.client and ble_manager.client.is_connected:
        # Executa a desconexão de forma síncrona no fechamento
        asyncio.run(ble_manager.disconnect())
    root.destroy()

root.protocol("WM_DELETE_WINDOW", on_closing)
# Inicia o loop da interface gráfica
root.mainloop()
