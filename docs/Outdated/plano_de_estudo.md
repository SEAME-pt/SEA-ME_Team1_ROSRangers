# Plano de estudo detalhado · Aerial Micro-Drone (8 semanas)

Pilotix Brook 2.5" (Betaflight, 4S) · ELRS 2.4 GHz · ESP8266 (ESP-12F) · ThoneFlow-3901U · VTX 5.8 GHz analógico + UVC · ROS 2 · Gazebo + PX4 SITL

---

## 0. Pressupostos

| Item | Valor |
|---|---|
| Ritmo | 5 dias por semana, 8 h por dia = 40 h por semana |
| Duração | 8 semanas (320 h no total; a estimativa do conteúdo é 170 a 300 h, o resto é folga e documentação) |
| Divisão diária padrão | 3 h estudo + 4 h prática + 1 h documentação |
| Língua dos registos | Português (README e diário), código comentado em inglês |

**Divisão diária sugerida**

| Hora | Atividade |
|---|---|
| 1 h | Revisão do dia anterior + objetivo do dia (escrever 3 linhas) |
| 2 h | Estudo (leitura, vídeos, documentação oficial) |
| 4 h | Prática com hardware ou simulação (com pausas de 10 min a cada 90 min) |
| 1 h | Documentar: diário, fotos, logs, commit no Git |

**Se o teu período for mais curto**: corta primeiro a Semana 7 (perceção) e depois a Semana 6 (simulação) para versão mínima. Nunca cortes a Semana 1 nem a 8 (segurança e failsafe).

**Dependências que ainda não confirmei**: bateria CNHL 4S 850 mAh disponível, regras do espaço de voo (gaiola/rede), versão do ESP-12F (módulo nu ou placa com USB), distro ROS 2 usada pela turma.

---

## 1. Regras de segurança (valem em todas as semanas)

1. **Hélices fora** em qualquer teste de bancada. Só se montam para o voo.
2. **Primeiro sem bateria** (USB), depois com bateria e sem hélices, só no fim com hélices.
3. **Checklist antes de armar**: bateria carregada e inspecionada, hélices apertadas e no sentido certo, failsafe testado, switch de kill (ARM) à mão, zona livre, pessoas afastadas.
4. **Failsafe antes de autonomia**: a rádio (ELRS) tem de poder cortar motores a qualquer momento.
5. **LiPo**: carregar em saco/caixa ignífuga, nunca sem vigilância, nunca abaixo de 3,5 V por célula em voo (alarme).
6. **ESP8266 a 3,3 V**, nunca ligar 5 V aos pinos; confirmar tensão do pad do recetor antes de alimentar.
7. **Um erro, um registo**: todo o incidente vai para o diário com causa e correção.

---

## 2. Visão geral das 8 semanas

| Semana | Tema | Entregável da semana |
|---|---|---|
| 1 | Segurança, Betaflight, ELRS, primeiro voo manual | Drone a voar em manual + checklist + failsafe testado |
| 2 | Série, MSP e ponte ESP8266 | Ponte UART para Wi-Fi a falar com o FC |
| 3 | ROS 2 base no computador de terra | Pacote ROS 2 com nós, tópicos e rosbag |
| 4 | Sensores e estimação de posição | Optical flow lido e velocidade estimada |
| 5 | Controlo | Controlador em cascata em simulação simples |
| 6 | Simulação Gazebo + PX4 SITL | Missão em SITL com a mesma interface ROS |
| 7 | Câmara e perceção | Vídeo UVC com latência medida e deteção simples |
| 8 | Missão, failsafe, integração, documentação | Demo final + relatório + gap simulação/real |

---

## 3. Semana 1 · Segurança, Betaflight, ELRS e voo manual

**Objetivo**: perceber a cadeia rádio, FC, motores, e ter o drone configurado e a voar em manual com failsafe validado.

**Resultado esperado no fim da semana**: configuração Betaflight guardada (`diff all`), bind ELRS feito, motores testados sem hélices, failsafe verificado, 1 voo manual curto.

### Dia 1 · Hardware e segurança
- Estudo (3 h): anatomia de um quad (FC, ESC, motores, VTX, RX), 4S LiPo (tensão, C, carga, armazenamento), regras de segurança acima.
- Prática (4 h): rever os teus README de hardware; inspecionar o drone (soldaduras, fios, hélices, direção dos motores no esquema); montar o carregador e o saco ignífugo; checklist de pré-voo escrita à mão.
- Documentar (1 h): diário do dia 1, fotos do drone, checklist em Markdown.

### Dia 2 · Betaflight Configurator
- Estudo: abas Ports, Configuration, Receiver, Modes, Motors, CLI.
- Prática: ligar por USB (Ubuntu: verificar `dialout` e regras udev), `dump`/`diff all` guardados, confirmar versão do firmware e target no CLI (`version`), orientação do gyro no 3D.
- Documentar: guardar o `diff all` inicial no Git.

### Dia 3 · ELRS: bind e link
- Estudo: como funciona o ELRS (2.4 GHz, pacotes, taxa, potência), CRSF, bind phrase. Fonte: [binding ELRS](https://www.expresslrs.org/quick-start/binding/) e [guia Oscar Liang](https://oscarliang.com/bind-expresslrs-receivers).
- Prática: configurar UART com Serial RX + CRSF, bind do Nano RX à Pocket, ver canais no separador Receiver, mapear ARM num switch, mapear um switch de **kill** claro.
- Nota: confirmar a tensão do pad V do recetor antes de alimentar (ver README de hardware).
- Documentar: tabela de canais e switches.

### Dia 4 · Motores, failsafe e OSD
- Estudo: protocolo DShot, ordem e direção dos motores, tipos de failsafe (Stage 1 e Stage 2).
- Prática: **sem hélices**, testar cada motor no separador Motors com bateria; configurar failsafe (desligar motores quando o link se perde); testar desligando o TX com o drone armado em bancada **sem hélices**; configurar OSD e alarme de bateria.
- Documentar: vídeo curto do teste de failsafe, resultado em tabela.

### Dia 5 · Primeiro voo e revisão
- Estudo (1 h): ler logs/Sensors do Betaflight, o que é um tune.
- Prática: voo de hover curto em modo Angle, a baixa altura, em espaço seguro; aterrar cedo; verificar temperatura de motores e bateria.
- Revisão semanal (2 h): o que correu mal, o que falta; atualizar o diário.
- Documentar: relatório da semana 1 (1 página).

**Critério de passagem**: o drone arma, voa em Angle, corta motores se o rádio for desligado.

---

## 4. Semana 2 · UART, MSP e ponte ESP8266

**Objetivo**: ligar o ESP-12F a uma UART do FC e fazer uma ponte transparente UART para Wi-Fi, falando MSP.

### Dia 6 · Série e MSP
- Estudo: UART (baud, 8N1, níveis lógicos 3,3 V), estrutura de uma trama MSP v1/v2 (cabeçalho, tamanho, comando, payload, checksum).
- Prática: no PC, usar um adaptador USB-serial (ou o próprio USB do FC) e ler `MSP_API_VERSION`, `MSP_ATTITUDE`, `MSP_STATUS` em Python (`pyserial`).
- Documentar: script `msp_read.py` comentado.

### Dia 7 · ESP-12F em bancada
- Estudo: pinout ESP-12F, **3,3 V**, picos de corrente (~300 mA em TX Wi-Fi), modos de arranque (GPIO0 baixo e GPIO15 baixo, EN alto para flash).
- Prática: ligar o módulo a um adaptador USB-serial 3,3 V com regulador adequado, flashar um "blink" e um "hello Wi-Fi" (Arduino core ESP8266 ou PlatformIO).
- Atenção: se o teu ESP-12F é módulo nu, precisa de resistências de pull-up/pull-down e alimentação estável (condensador junto da alimentação).
- Documentar: esquema à mão (foto) das ligações.

### Dia 8 · Ponte UART para UDP/TCP
- Estudo: sockets UDP vs TCP, latência, perdas, buffer.
- Prática: firmware do ESP com ponte transparente (UART0 para UDP), testar loopback com o PC (eco). Medir latência ida e volta.
- Documentar: tabela de latências (média, máximo, perda).

### Dia 9 · Ligar ao FC
- Estudo: Betaflight Ports, MSP numa UART, pads livres R1/T1 do teu FC (UART1).
- Prática: ligar T1/R1 ao ESP (TX do FC ao RX do ESP e vice-versa, GND comum, 3,3 V), ativar MSP a 115200 em UART1, ler `MSP_ATTITUDE` pelo Wi-Fi no PC. **Sem hélices.**
- Documentar: captura da telemetria.

### Dia 10 · Coexistência e revisão
- Estudo: Wi-Fi 2,4 GHz vs ELRS 2,4 GHz, escolha de canal, efeito do carbono (atenuação).
- Prática: medir alcance e perdas com o ESP montado no frame e o ELRS ativo; testar canais Wi-Fi 1, 6, 11; verificar se o link ELRS degrada (LQ no OSD).
- Revisão semanal e relatório.

**Critério de passagem**: telemetria MSP do FC chega ao PC por Wi-Fi com latência medida e sem afetar o link ELRS.

---

## 5. Semana 3 · ROS 2 base

**Objetivo**: dominar o essencial de ROS 2 no computador de terra (Ubuntu).

> Distro: usa a que a turma indicar. Se não houver indicação, escolhe uma versão LTS e confirma em [docs.ros.org](https://docs.ros.org/en/lyrical/The-ROS2-Project/Release-Schedule.html) qual está suportada hoje, antes de instalar.

### Dia 11 · Instalação e conceitos
- Estudo: nós, tópicos, serviços, ações, parâmetros, colcon, workspace.
- Prática: instalar ROS 2, tutoriais "Beginner: CLI tools".
- Documentar: notas de instalação (versão, comandos).

### Dia 12 · Pacotes em Python
- Estudo: `rclpy`, publishers/subscribers, mensagens padrão.
- Prática: criar pacote `drone_bridge` com um nó que publica um contador, outro que subscreve.

### Dia 13 · QoS, launch, parâmetros
- Estudo: QoS (reliable vs best effort, depth), launch files, YAML de parâmetros.
- Prática: launch com 2 nós e parâmetros; observar perdas com QoS diferentes.

### Dia 14 · tf2 e rosbag
- Estudo: frames (map, odom, base_link), transformações; gravação e replay.
- Prática: publicar um `tf` simples e gravar com `ros2 bag record`; reproduzir.

### Dia 15 · Nó MSP para ROS 2
- Prática: nó `msp_node` que lê a ponte (UDP) e publica `sensor_msgs/Imu` ou atitude em tópicos; teste com o FC ligado, **sem hélices**.
- Revisão semanal e relatório.

**Critério de passagem**: telemetria do FC a aparecer em tópicos ROS 2 e gravada em rosbag.

---

## 6. Semana 4 · Sensores e estimação

**Objetivo**: ler o ThoneFlow-3901U e estimar velocidade/posição relativa.

> Incerteza: não confirmei o protocolo, baud rate nem se o ThoneFlow mede altura. Primeiro dia desta semana: ler a documentação/ficha do fabricante e, se faltar, usar um analisador lógico ou osciloscópio para ver a trama.

### Dia 16 · Documentar o sensor
- Estudo: princípio do optical flow (deslocamento de pixels, escala com altura), PMW3901 como referência ([docs PX4 PMW3901](https://docs.px4.io/v1.12/en/sensor/pmw3901), não específico do ThoneFlow).
- Prática: ligar o sensor a um adaptador USB-serial, ver bytes crus, identificar baud e cabeçalho.

### Dia 17 · Decodificar
- Prática: parser Python da trama, publicar `flow_x`, `flow_y`, qualidade.
- Documentar: especificação da trama que descobriste.

### Dia 18 · Caminho do sensor até ao PC
- Estudo: opções D3 (ESP segunda UART, SoftwareSerial, outra ponte).
- Prática: escolher e implementar uma; medir taxa e perdas.

### Dia 19 · Estimação
- Estudo: filtro complementar, Kalman, integração com IMU; problema da altura (escala do flow).
- Prática: estimar velocidade horizontal em bancada movendo o drone sobre uma superfície com textura.

### Dia 20 · Validação
- Prática: comparar a estimativa com um percurso medido (régua ou fita no chão); calcular erro.
- Revisão semanal e relatório.

**Critério de passagem**: velocidade horizontal estimada com erro conhecido e documentado.

---

## 7. Semana 5 · Controlo

**Objetivo**: implementar um controlador em cascata e entender o caminho dos setpoints.

> Decisão D2: setpoints via MSP (`MSP_SET_RAW_RC` / MSP Override) mantendo o ELRS como caminho manual e kill. Não confirmei que o MSP Override funciona na 4.5.1 do teu FC. Ver [guia Betaflight](https://betaflight.com/docs/wiki/guides/current/MSP-Companion-Computer-Control). Dia 21 serve para testar isto **sem hélices** antes de planear mais nada.

### Dia 21 · Testar o MSP Override
- Estudo: guia acima, modo MSP Override, prioridade com o RX.
- Prática: bancada, sem hélices; enviar canais por MSP e ver no Receiver; testar que o switch no TX retoma o controlo manual.
- Decisão: se não funcionar, plano B (emular TX via CRSF ou outro firmware); registar a decisão.

### Dia 22 · PID e cascata
- Estudo: PID, windup, filtros derivativos, cascata posição, velocidade, atitude.
- Prática: simulador Python simples de um ponto de massa; sintonizar.

### Dia 23 · Altitude
- Estudo: porquê é a parte mais difícil (sem sensor de altura fiável): throttle como saída, hover throttle estimado.
- Prática: modelo simples e estratégia (altura via sensor, ou modo altitude manual com ajuste só horizontal).

### Dia 24 · Controlador como nó ROS 2
- Prática: nó `controller` com subscrição de estado e publicação de setpoints; testes unitários.

### Dia 25 · Segurança no controlo
- Prática: limites (saturação, taxa), watchdog (se não chegam setpoints por X ms, devolver ao RX), kill switch.
- Revisão semanal.

**Critério de passagem**: controlador em simulação simples estável e com watchdog.

---

## 8. Semana 6 · Simulação (Gazebo + PX4 SITL)

**Objetivo**: ter a mesma interface ROS 2 a falar com um simulador e depois com o real.

> Incerteza: PX4 no F722 de 512 KB provavelmente não cabe; o PX4 aqui é só em simulação. Ver [Offboard com ROS 2](https://docs.px4.io/main/en/ros2/offboard_control).

### Dia 26 · Instalar
- Prática: PX4-Autopilot, Gazebo, SITL de quadrotor; correr o exemplo base.

### Dia 27 · Offboard via ROS 2
- Estudo: uXRCE-DDS, tópicos `px4_msgs`, offboard heartbeat.
- Prática: exemplo oficial: armar, descolar, hover.

### Dia 28 · Interface comum
- Estudo: padrão de adaptadores (backend sim vs backend real).
- Prática: definir interface (`state`, `setpoint`, `arm`, `kill`) com dois backends: PX4 e MSP.

### Dia 29 · Missão em simulação
- Prática: missão quadrada com o teu controlador; gravar rosbag.

### Dia 30 · Falhas em simulação
- Prática: simular perda de link, atraso, ruído do sensor; ver reação.
- Revisão semanal e relatório.

**Critério de passagem**: a mesma missão corre em SITL pela interface comum.

---

## 9. Semana 7 · Câmara e perceção

**Objetivo**: capturar vídeo do VTX por UVC, medir latência e fazer deteção simples.

### Dia 31 · Captura UVC
- Estudo: V4L2, formatos, OpenCV.
- Prática: capturar com `cv2.VideoCapture`, listar dispositivos (`v4l2-ctl`).

### Dia 32 · Latência
- Prática: medir latência vidro a vidro (cronómetro no ecrã filmado pela câmara); comparar resoluções.

### Dia 33 · Nó ROS 2 de imagem
- Prática: publicar `sensor_msgs/Image`, `cv_bridge`; gravar bag.

### Dia 34 · Deteção simples
- Prática: marcador (ArUco/AprilTag) ou cor; publicar pose relativa.

### Dia 35 · Raspberry Pi 5 + Hailo (opcional)
- Prática: se disponível, correr um modelo no Hailo; senão, ficar em OpenCV clássico.
- Revisão semanal.

**Critério de passagem**: vídeo no ROS 2 com latência medida e uma deteção funcional.

---

## 10. Semana 8 · Missão, failsafe e documentação

### Dia 36 · Máquina de estados
- Estados: IDLE, ARMED, TAKEOFF, HOVER, MISSION, LAND, FAILSAFE. Transições e condições.

### Dia 37 · Failsafe completo
- Perda de link ELRS, perda Wi-Fi, perda do flow, bateria baixa, watchdog. Testar cada um em simulação e, onde seguro, em bancada.

### Dia 38 · Integração em bancada
- Tudo ligado, **sem hélices**: ver cadeia completa do PC ao FC.

### Dia 39 · Voo de demonstração (só se tudo anterior passou)
- Checklist completa, espaço protegido, kill pronto, voo curto assistido.

### Dia 40 · Documentação final
- Relatório: arquitetura, decisões D1 a D3 e porquê, medições, **gap simulação vs real**, lições, próximos passos.

---

## 11. Entregáveis finais

- Repositório Git com código, `diff all` do Betaflight, README em PT e EN
- Diário diário e relatórios semanais
- Medições: latência Wi-Fi, latência vídeo, erro do flow
- Vídeos: failsafe, voo manual, simulação, demo
- Relatório final com gap simulação para real

---

## 12. Riscos e planos B

| Risco | Impacto | Plano B |
|---|---|---|
| MSP Override não funciona na 4.5.1 | Setpoints por Wi-Fi bloqueados | Emular canais via CRSF/TX; ou INAV (target do FC não confirmado) |
| ThoneFlow sem altura/documentação | Estimação fraca | Altitude manual, só controlo horizontal |
| Wi-Fi degrada o ELRS | Perda de link | Mudar canal, reduzir potência, afastar antena |
| Sem bateria 4S | Sem voo | Fazer tudo em bancada e simulação |
| Latência alta do vídeo | Perceção lenta | Resolução menor, só debug em terra |
| PX4 não cabe no F722 | Sem PX4 real | PX4 só em SITL (já é o plano) |

---

## 13. Recursos

- [Betaflight: MSP Companion Computer Control](https://betaflight.com/docs/wiki/guides/current/MSP-Companion-Computer-Control)
- [PX4: Offboard Control com ROS 2](https://docs.px4.io/main/en/ros2/offboard_control)
- [PX4: sensor PMW3901](https://docs.px4.io/v1.12/en/sensor/pmw3901)
- [ExpressLRS: Binding](https://www.expresslrs.org/quick-start/binding/)
- [Oscar Liang: Bind ELRS](https://oscarliang.com/bind-expresslrs-receivers)
- [ROS 2: calendário de versões](https://docs.ros.org/en/lyrical/The-ROS2-Project/Release-Schedule.html)

---

## 14. Checklist semanal

- [ ] Objetivo da semana cumprido
- [ ] Diário completo
- [ ] Código no Git
- [ ] Incidentes registados
- [ ] Medições guardadas
- [ ] Segurança revista

## 15. Glossary

| Term | Meaning |
| --- | --- |
| **Commit** | A recorded snapshot of changes in a version-control repository. |
| **CRSF** | Crossfire Serial Protocol, a serial protocol used by radio-control equipment. |
| **ESC** | Electronic Speed Controller: hardware that drives a motor. |
| **ESP8266** | A WiFi-enabled microcontroller used as the drone communication module. |
| **Failsafe** | An automatic safe response when control, communication, or a sensor fails. |
| **FC** | Flight Controller: the onboard controller responsible for stabilization and control. |
| **MSP** | MultiWii Serial Protocol, used for communication with Betaflight flight controllers. |
| **ROS 2** | The second-generation Robot Operating System middleware used for robotics integration. |
| **TCP** | Transmission Control Protocol: reliable, ordered, connection-oriented transport. |
| **UDP** | User Datagram Protocol: connectionless transport suited to refreshed, time-sensitive data. |
| **UART** | Universal Asynchronous Receiver/Transmitter: an asynchronous serial interface. |
| **VTX** | Video transmitter: hardware that sends the aircraft video feed. |
| **API** | Application Programming Interface: a defined way for software components to communicate. |
| **PR** | Pull Request: a proposed change submitted for review before merging. |
