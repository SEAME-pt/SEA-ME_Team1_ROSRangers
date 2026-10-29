13/Oct - 10:50/11:17
Guilherme - Começar Pipeline
Paulo/Ale - Começar a trabalhar com o drone/ESP
Paulo - Pesquisa mais a funda dos UARTS
Bruno - Continua Gesture Recognition Program
Ler Safety Rules
Overview das Safety Rules
Perguntas sobre "Cage" e Hélices
Bruno Flight Pilot decidido

14/Oct - 11:58/12:11
Guilherme - Progresso nas issues/PR
Paulo - Possibilidade de live sync no obsidian
Prep Weekly(14/Oct)
- Como correu a semana + proxima semana
- Falar das docs
- Falar do CI/CD
- Bruno esta a trabalhar com o Gesture Capture e ROS2
- Paulo e Ale testar ESP(WIFI)
- Thiago soldar modulo antena(ELRS)
Ale - Perguntar como esta o progresso da equipa na weekly

16/Oct - 15:58/16:12
Falar sobre a apresentaçao da sprint
Falar de weeklys, o que contar com ela
Mesma idea para a weekly do que foi combinado no dia 14/Oct
Como funciona as weeklys, sprints, retrospective.
Paulo - Continua a trabalhar com o ESP e vai avançar com o backup do firmware e do FC.
Bruno - Trabalhar com o ROS2, gestures e nodes
Tiago - Soldar e perceção de hardware mais a fundo. Vai investigar conexão entre o comando e o drone.
Guilherme - Melhorias docs e ajuda com os outros elementos.


19/Oct - 09:36/09:50
Ale saiu do grupo.
Perceber como vai proceder relativamente ao grupo agora com 4 pessoas.
Falar com a Maria relativamente a este situação.
Planeamento da sprint
	Mostrar a board do github
	Documentação
Paulo - Estudar hardware e progredir com os testes no drone.
Thiago - Avançar com a documentação do FPV e testes relacionados com ele
Bruno - Avanço nos nodes e talvez radio
Guilherme - Verificar motores, acrescentar CI/CD pipelines.

20/Oct - 09:06/09:30
Planeamento de sprint Overview
	Tiago vai verificar o Gazebo
	Bruno vai entrar no Gazebo tambem, continuar a trabalhar nos fail safes
	Guilherme CI/CD e ajudar malta, melhorar o readme.
Paulo - Juntar o grupo em pares, Guilherme e Bruno trabalhar nos nodes e Paulo e Thiago trabalhar diretamente no drone.
Guilherme - Atrasar o HIL para a próxima sprint.
Guilherme - Estudar mais um pouco a AI, avançar com multi-agent.
Objetivos da semana
Paulo - Investigar diff de latencias
Bruno - Mais sinais.

21/Oct - 12:26/12:34
Problemas com a bateria no dia anterior. Ficamos sem uma bateria.(As duas equipas ficaram sem uma bateria - Total 2 baterias, 1 para cada equipa)
Problemas com comando/bateria.
Atualização possivel de firmware.
Weekly hoje - Paulo vai
Bruno - Continuar a ROS/Nodes - Containers
Paulo - Continuação dos testes de latência.
Guilherme - Ajudar os outros elementos.
Tiago - Investigação dos parametros. Gazebo.

22/Oct - 09:46/09:50
Thiago - Gazebo e ajudar Paulo na ligacao entre o ESP e o FC
Paulo - Continuar a trabalhar no ESP e tentar melhorar a performance do ESP.
Bruno - Continuar node-flight
Guilherme - HIL runner, e preparar a proxima sprint
**Blockers**
Tiago - Radio

23/Oct - 10:52/10:58
Paulo - Vai continuar testar conexoes/benchmark
Thiago - Recalibrar motores e Gazebo
Bruno - confirmar mission node conexecao com os outros nodes, implementar failsafes nos nodes.
Guilherme - Ler documentacao
**Blockers**
Paulo - Verificar os testes por causa de problemas de cabos no dia anterior.

26/Oct - 15:47:15:56
Bruno - Update ao gestures para processamentos de mais gestoe + scalability para os dois braços
Guilherme - HIL a funcionar, com a funcionar
Paulo - Sprint 07 -  UDP e TCP ao mesmo tempo, em principio está a funcionar, problemas relacionados com cabos, a realizar os testes de momento.
Tiago - Gazebo operacional e comunicação com ele operacional. Comunicação com o firmware.(tentativa).
**Blockers**
Bruno - Falta de confiança nos gestures, testar mais a fundo com eles.

28/Oct - 10:55:11:00
Bruno - Continuar a trabalhar nos nodes e melhorar los.
Paulo - Ajudar Tiago relativamente as latencias do drone e comunicaçao com os parametros do drone.
Tiago - Paramentros de safety do drone para fazer o drone voar em principio hoje
Guilherme - Trabalhar diretamente com o drone(hélices, bateria, etc etc)
A equipa quer colocar o drone no primeiro voo hoje.
**Blockers**
Tiago - FC e latencias( Ajuda do Paulo hoje)

29/Oct - 12:37/12:46
Tiago - Considerações da weekly e obter telemetria da blackbox.
Guilherme - Preparação da retroespective.
Bruno - Preparar parte da apresentação da retrospective relativamente aos nodes.
Paulo - Ajudar o Tiago com o drone.


---

## Glossary

| Term       | Meaning                                                                                |
| ---------- | -------------------------------------------------------------------------------------- |
| **Daily**  | The short stand-up where each person says what they did and what blocks them.          |
| **Weekly** | The weekly team meeting with progress, docs and CI/CD status.                          |
| **CI/CD**  | Continuous Integration and Delivery: automated build, test and release pipeline.       |
| **ESP**    | The ESP8266 WiFi microcontroller used as the drone communication module.               |
| **UART**   | Universal Asynchronous Receiver/Transmitter: an asynchronous serial interface.         |
| **ELRS**   | ExpressLRS, the open source radio-control link used on our receiver.                   |
| **ROS 2**  | The second-generation Robot Operating System middleware used for robotics integration. |
| **PR**     | Pull Request: a proposed change submitted for review before merging.                   |
