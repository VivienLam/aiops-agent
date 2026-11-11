# aiops-agent

Un agent LLM qui diagnostique les pannes de l'[OpenTelemetry Demo](https://github.com/open-telemetry/opentelemetry-demo) (une fausse boutique en ligne faite d'une quinzaine de microservices) à partir de ses métriques, logs et traces. L'agent passe par un serveur MCP que j'écris moi-même.

Projet en cours.

La question qui m'intéresse : un agent d'exploitation qui lit des logs peut-il être manipulé par du texte qu'un attaquant y fait entrer ? Si oui, combien d'attaques mes protections arrêtent, et qu'est-ce qu'elles coûtent en précision de diagnostic ?

## Où j'en suis

- [x] OpenTelemetry Demo lancée sous WSL2 avec Docker
- [x] Serveur MCP avec un premier outil, `get_service_health` : trafic, taux d'erreur et latence p95 d'un service, calculés à partir des traces
- [ ] Autres outils de lecture : logs, traces, état des conteneurs
- [ ] Agent (LangGraph) avec un rapport en JSON
- [ ] Banc de pannes injectées et évaluation chiffrée
- [ ] Attaques par injection de prompt via les logs, puis protections

## Lancer

Il faut que l'OpenTelemetry Demo tourne (`make start` dans son dossier) et que [uv](https://docs.astral.sh/uv/) soit installé.

    uv sync

Le serveur MCP se lance ensuite depuis un client MCP. Je l'ai testé avec Claude.

J'ai dû modifier deux réglages de la démo pour qu'elle tourne sur ma machine (voir `docs/DECISIONS.md`). Ils ne sont pas encore dans ce dépôt.

## Choix techniques

Chaque choix, les alternatives et la raison sont dans [docs/DECISIONS.md](docs/DECISIONS.md).
