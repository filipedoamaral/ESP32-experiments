import tkinter as tk
import threading
import asyncio
from datetime import datetime, timedelta
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

# --- Armazenamento de Dados Históricos ---
# Armazena todos os dados recebidos como {'timestamp': dt, 'bpm': val, 'oxy': val}
historical_data = []
realtime_data = deque([], maxlen=50) # Para a visualização em tempo real
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

            # 2. Armazena o dado com timestamp
            data_point = {'timestamp': datetime.now(), 'bpm': bpm_value, 'oxy': oxy_value}
            historical_data.append(data_point)
            realtime_data.append(data_point) # Adiciona também à deque de tempo real
            
            # 3. Armazena os últimos valores para o label numérico
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

def get_data_for_timescale(scale):
    """Filtra e agrega dados com base na escala de tempo selecionada."""
    now = datetime.now()
    
    if scale == "realtime":
        # Retorna os últimos 50 pontos diretamente
        return list(realtime_data)

    elif scale == "hour":
        time_delta = timedelta(hours=1)
        aggregation_minutes = 1 # Agrega a cada minuto
    elif scale == "day":
        time_delta = timedelta(days=1)
        aggregation_minutes = 15 # Agrega a cada 15 minutos
    else:
        return []

    # Filtra dados no intervalo de tempo
    start_time = now - time_delta
    filtered = [d for d in historical_data if d['timestamp'] >= start_time]

    if not filtered:
        return []

    # Agrega os dados
    aggregated_data = {}
    for point in filtered:
        # Chave de agregação (arredonda para o intervalo de minutos)
        ts = point['timestamp']
        key_ts = ts - timedelta(minutes=ts.minute % aggregation_minutes,
                                seconds=ts.second,
                                microseconds=ts.microsecond)
        
        if key_ts not in aggregated_data:
            aggregated_data[key_ts] = {'bpm': [], 'oxy': []}
        
        aggregated_data[key_ts]['bpm'].append(point['bpm'])
        aggregated_data[key_ts]['oxy'].append(point['oxy'])

    # Calcula a média para cada bloco agregado
    result = []
    for ts, values in sorted(aggregated_data.items()):
        avg_bpm = sum(values['bpm']) / len(values['bpm'])
        avg_oxy = sum(values['oxy']) / len(values['oxy'])
        result.append({'timestamp': ts, 'bpm': avg_bpm, 'oxy': avg_oxy})
        
    return result

def update_plot_ui():
    """Atualiza o gráfico com base na escala de tempo selecionada."""
    selected_scale = timescale_var.get()
    plot_data = get_data_for_timescale(selected_scale)

    if not plot_data:
        # Limpa o gráfico se não houver dados
        line_bpm.set_data([], [])
        line_oxy.set_data([], [])
    else:
        timestamps = [d['timestamp'] for d in plot_data]
        bpm_values = [d['bpm'] for d in plot_data]
        oxy_values = [d['oxy'] for d in plot_data]

        line_bpm.set_data(timestamps, bpm_values)
        line_oxy.set_data(timestamps, oxy_values)
        ax.relim()
        ax.autoscale_view()

    try:
        canvas.draw_idle()
    except Exception as e:
        print(f"Erro ao atualizar gráfico: {e}")

    last_value_label.config(text=
        f"BPM: {last_bpm:.0f} | "
        f"Oxigenação: {last_oxy:.1f}%"
    )

def schedule_plot_update():
    """Agenda a atualização do gráfico para evitar sobrecarga na UI."""
    global update_job
    if update_job:
        root.after_cancel(update_job)
    # Agenda a atualização para ocorrer em 500ms para não sobrecarregar
    update_job = root.after(500, update_plot_ui)

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

ax.grid(True, linestyle='--', alpha=0.6) # Adiciona uma grade para melhor leitura
ax.set_title("Monitoramento de Sensores em Tempo Real")
ax.set_ylabel("Valor do Sensor")
ax.set_xlabel("Tempo")

# Cria os TRÊS objetos de linha que serão atualizados
line_bpm, = ax.plot([], [], label='BPM', color='blue', marker='o', markersize=2, linestyle='-')
line_oxy, = ax.plot([], [], label='Oxigenação (%)', color='green', marker='o', markersize=2, linestyle='-')

# Adiciona a legenda para identificar as linhas
ax.legend(loc='upper left')

# Formata o eixo X para exibir datas de forma legível
fig.autofmt_xdate()

# 2. Integra a figura ao Tkinter
canvas = FigureCanvasTkAgg(fig, master=root)
canvas_widget = canvas.get_tk_widget()
canvas_widget.pack(padx=10, pady=10)

# --- Frame para Controles do Gráfico (Seleção de Tempo) ---
controls_frame = tk.Frame(root)
controls_frame.pack(pady=(0, 5))

timescale_var = tk.StringVar(value="realtime")

tk.Radiobutton(controls_frame, text="Tempo Real", variable=timescale_var, value="realtime", command=update_plot_ui).pack(side=tk.LEFT)
tk.Radiobutton(controls_frame, text="Última Hora", variable=timescale_var, value="hour", command=update_plot_ui).pack(side=tk.LEFT)
tk.Radiobutton(controls_frame, text="Último Dia", variable=timescale_var, value="day", command=update_plot_ui).pack(side=tk.LEFT)

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
