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

## Noms de services filtrés aussi en sortie (list_services)
- Alternative : renvoyer tels quels les noms trouvés dans Prometheus
- Raison : ces noms viennent de la télémétrie, que chaque service envoie lui-même. Un service piégé pourrait mettre du texte d'injection dans son nom. La même regex qu'en entrée écarte tout nom bizarre, et l'outil dit combien il en a écarté

## État des conteneurs lu avec le SDK docker, limité au projet de la démo
- Alternatives : lancer la commande `docker stats` et lire son texte, ou passer par cAdvisor dans Prometheus
- Raison : le SDK donne directement l'état, la santé, les redémarrages et l'OOM, sans rien installer de plus. Le filtre sur l'étiquette compose `opentelemetry-demo` empêche l'agent de regarder les autres conteneurs de la machine

## Mémoire : lecture instantanée, sans le cache
- Alternative : la lecture standard de `stats`, qui attend environ 2 s par conteneur pour mesurer aussi le CPU
- Raison : `one_shot=True` répond en quelques ms, ce qui donne les 28 conteneurs en moins d'une seconde. On retire le cache fichier (`inactive_file`), comme `docker stats`, sinon un service qui lit beaucoup de fichiers aurait l'air de saturer sa mémoire

## Outil de logs sans protection pour l'instant
- Alternative : filtrer ou baliser le texte des logs dès maintenant (retirer les phrases qui ressemblent à des consignes, encadrer les logs comme « données non fiables »)
- Raison : c'est la question principale du projet. Il me faut d'abord une version sans défense pour mesurer à quel point l'injection marche, puis ajouter les protections une par une et mesurer ce que chacune apporte. Seules les entrées de l'outil sont vérifiées (nom de service, sévérité dans une liste fermée, texte par regex) : ça protège la requête OpenSearch, pas le LLM, donc ça ne fausse pas la mesure

## Logs : sévérité lue en nombre, regroupement en Python
- Alternatives : filtrer sur le texte de la sévérité, regrouper avec une agrégation OpenSearch
- Raison : le texte change d'un service à l'autre (INFO, info, Information, Error…), le numéro OpenTelemetry est le même partout (13 = warn, 17 = error). OpenSearch ne sait pas regrouper les messages de plus de 256 caractères, donc je lis au plus 1000 logs (les plus graves d'abord) et je regroupe moi-même. Le détail d'une erreur est souvent dans exception.message plutôt que dans le message, je l'ajoute quand il apporte quelque chose

## Texte encodé dans une URL : invisible pour la recherche par mot (à garder en tête)
- Constat : une adresse passée dans l'URL de `/api/shipping` est stockée encodée (`%22ZZMARK-...%22`). OpenSearch découpe ça en un seul mot (`22zzmark`), donc `search_logs(text="ZZMARK")` ne la trouve pas. L'agent, lui, lit le texte s'il tombe sur ce log
- Rien à corriger maintenant
- Pour plus tard : une protection qui chercherait des mots-clés dans les logs serait facile à contourner avec de l'encodage (URL, base64, unicode). Il faudra décoder avant de filtrer, ou ne pas compter sur un filtre par mots

## Recherche de texte dans les logs : tous les mots (operator « and »)
- Alternative : le `match` par défaut, qui renvoie les logs contenant au moins un des mots
- Raison : « ZZMARK-urlquery » renvoyait tous les logs avec « zzmark ». Quand l'agent cherche une phrase, il veut les logs qui contiennent tous les mots

## Outils de traces (find_traces, get_trace) sans protection pour l'instant
- Alternative : filtrer les messages d'erreur et les noms de spans
- Raison : même règle que pour search_logs. Les messages d'exception contiennent du texte venu des visiteurs (voir ATTACK_SURFACE.md), je veux mesurer l'effet de l'injection avant d'ajouter des défenses. Seules les entrées sont vérifiées : nom de service par regex, trace_id en hexadécimal (16 à 32 caractères)

## Chemin critique et « temps propre » dans get_trace
- Alternative : donner l'arbre avec les durées et laisser le LLM conclure
- Raison : dans une trace, le proxy, le frontend et checkout ont tous une barre longue quand shipping est lent, parce qu'ils l'attendent. Un LLM risque d'accuser le premier span long qu'il voit. Comme dans l'interface de Jaeger, je pars de la fin du span racine et je remonte en suivant l'enfant qui finit le plus tard : c'est ce que le parent attendait. Le temps propre d'un span, c'est la part de sa durée où il n'attend aucun enfant du chemin. Testé avec intlShippingSlowdown : 5,4 s au total, et l'outil désigne shipping /ship-order avec 5 s de temps propre

## Arbre de trace compact
- Alternative : un span par ligne (125 lignes pour une commande, 5 000 caractères)
- Raison : sorties courtes. Trois règles : les spans identiques côte à côte sont regroupés (×3) ; un span qui ne fait que passer la main à un seul enfant du même service tient sur la même ligne ; les spans de moins de 5 % de la durée totale sont cachés, sauf en erreur. Un appel caché vers un autre service reste signalé (« calls shipping 9 ms ») pour que l'agent ne perde jamais un passage d'un service à l'autre. Une commande passe à environ 1 000 caractères

## Test automatique : tous les outils sont exposés
- Alternative : vérifier à la main
- Raison : j'ai déjà perdu un décorateur @mcp.tool() en modifiant server.py. La fonction marchait quand on l'appelait directement, mais l'agent ne la voyait plus. Le test démarre le serveur comme un vrai client MCP et compare la liste exacte des outils. Vérifié : il échoue si on retire un décorateur
