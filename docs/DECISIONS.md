# Décisions

Chaque choix, avec les alternatives et la raison.

## Application cible : OpenTelemetry Demo
- Alternatives : Online Boutique (Google), les applis d'AIOpsLab (sous Kubernetes)
- Raison : déjà instrumentée (logs, métriques, traces), tourne sous Docker Compose sur un PC, pannes intégrées pour démarrer

## Environnement : WSL2 + Docker installé dans Ubuntu
- Alternatives : Docker Desktop, dual boot Linux
- Raison : Docker était déjà installé dans Ubuntu et marchait. C'est plus léger que Docker Desktop, qui ajoute sa propre couche. WSL limité à 11 Go pour laisser de la place à Windows

## Modifications de la démo (mémoire du catalogue, dépendance au paiement)
- Alternative : ne rien toucher
- Raison : sur ce PC, le catalogue manquait de mémoire et le contrôle de santé du paiement bloquait tout le démarrage. Faites dans compose.yaml pour avancer ; à sortir dans un fichier à moi pour que le projet reste reproductible

## Pannes qui laissent un état à corriger
- Alternative : utiliser seulement les feature flags de la démo
- Raison : avec les flags, « réparer » revient à couper le flag, c'est-à-dire presque donner la réponse. Avec un faux déploiement bugué ou une mauvaise config, l'agent doit trouver le changement en cause et le défaire, comme en vrai

## Réparation jugée sur son effet
- Alternative : faire juger la description de la réparation par un LLM
- Raison : le taux d'erreur qui repasse sous le seuil est un critère objectif. Aucune interprétation possible

## Rapport en JSON, LLM-juge limité
- Alternative : LLM-juge pour tout
- Raison : le service et le type de panne se comparent automatiquement à la vérité terrain. Le juge ne sert que pour les preuves, et je mesure son accord avec mes propres annotations sur 30 rapports

## Test de renommage
- Raison : le repo de la démo est public, le LLM l'a sans doute vu à l'entraînement. Si le score chute avec des noms neutres (svc-07), l'agent reconnaissait la démo au lieu de diagnostiquer

## Test de la preuve retirée
- Raison : vérifier que l'agent ne devine pas. Sans la preuve clé, il doit répondre « je ne sais pas » ou « il me manque X »

## Mémoire des incidents avec lot d'apprentissage et lot de test
- Raison : si la mémoire contient le scénario testé, l'agent récite. On teste sur des variantes pour mesurer la généralisation

## RAG agentique
- Alternative : RAG classique, documents injectés à chaque appel
- Raison : l'agent choisit quand chercher. On compare les deux, c'est une ablation de plus

## Corpus utile plutôt que gros
- Alternative : des milliers de morceaux de doc générale
- Raison : ce qui aide vraiment à diagnostiquer, c'est la carte des dépendances (qui appelle qui) et des runbooks par symptôme. Postmortems gardés en lien + résumé perso, à cause des licences

## Question principale : injection de prompt via les logs
- Alternatives envisagées : contamination (diagnostic ou reconnaissance ?) en question principale, agent qui conclut trop tôt
- Raison : l'agent lit du texte qui peut venir d'un attaquant et il a le droit d'agir. C'est un vrai risque, et ça donne du poids à la partie « actions sécurisées ». La contamination est gardée comme mesure de rigueur

## Serveur MCP : le mien plutôt que celui de la démo
- Alternative : réutiliser le serveur MCP fourni par la démo (mode agentique, `make start-agentic`)
- Raison : ses outils servent à faire des achats (produits, panier, paiement), comme un client. Aucun ne lit les métriques, les logs ou les traces. Je garde de leur agent l'idée de LiteLLM pour changer de LLM par la config

## SDK MCP officiel, version 2 (MCPServer)
- Alternatives : le paquet fastmcp séparé (utilisé par la démo), ou rester sur le SDK 1.x
- Raison : la v1 n'a plus que des correctifs de sécurité. En v2, FastMCP s'appelle MCPServer mais les outils s'écrivent pareil. Une dépendance de moins que fastmcp

## uv pour l'environnement Python
- Alternative : pip + venv
- Raison : plus rapide, et le fichier uv.lock fige les versions exactes. Quelqu'un qui clone le repo retrouve le même environnement

## Métriques d'erreurs et de latence tirées des traces
- Alternative : les métriques HTTP et gRPC propres à chaque service
- Raison : les span metrics du collecteur ont le même format pour tous les services, quel que soit leur langage. Je ne compte que les spans « serveur », pour ne pas compter deux fois une même requête

## Noms de services vérifiés avant d'entrer dans une requête
- Alternative : faire confiance à ce que le LLM envoie
- Raison : le nom vient du LLM, qui peut avoir lu un texte piégé dans les logs. Il est collé dans une requête PromQL, donc une regex stricte (lettres, chiffres, tirets) bloque toute modification de la requête. Première protection côté outils. Testée : « svc; drop » est refusé

## Code et descriptions des outils en anglais
- Alternative : tout en français
- Raison : c'est la norme sur GitHub, et le LLM lit les descriptions des outils pour choisir lequel appeler. L'anglais est la langue où il est le plus fiable. Les docs du projet restent en français

## Code sur GitHub dès le début
- Alternative : publier à la fin, une fois le projet propre
- Raison : l'historique des commits montre comment le projet a avancé. Le suivi perso et les notes sur ma machine restent hors du dépôt
