# Décisions

Chaque choix, avec les alternatives et la raison.

## Application cible : OpenTelemetry Demo
- Alternatives : Online Boutique (Google), les applis d'AIOpsLab (sous Kubernetes)
- Raison : déjà instrumentée (logs, métriques, traces), tourne sous Docker Compose sur un PC, pannes intégrées pour démarrer

## Environnement : WSL2 + Docker installé dans Ubuntu
- Alternatives : Docker Desktop, dual boot Linux
- Raison : Docker était déjà installé dans Ubuntu et marchait. C'est plus léger que Docker Desktop, qui ajoute sa propre couche. WSL limité à 11 Go pour laisser de la place à Windows

## Modifications de la démo (mémoire du catalogue et de checkout, dépendance au paiement)
- Alternative : ne rien toucher
- Raison : sur ce PC, le catalogue et checkout manquaient de mémoire, et le contrôle de santé du paiement bloquait tout le démarrage. D'abord faites dans compose.yaml pour avancer, puis sorties dans demo/compose.local.yaml (voir « Fichier compose à moi »)

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
- Raison : dans une trace, le proxy, le frontend et checkout ont tous une barre longue quand shipping est lent, parce qu'ils l'attendent. Un LLM risque d'accuser le premier span long qu'il voit. Comme dans l'interface de Jaeger, je pars de la fin du span racine et je remonte en suivant l'enfant qui finit le plus tard : c'est ce que le parent attendait. Le temps propre d'un span, c'est la part de sa durée où il n'attend aucun enfant du chemin. Testé avec intlShippingSlowdown : 5,4 s au total, et l'outil désigne shipping /ship-order avec 5 s de temps propre. Calcul du temps propre revu depuis, voir « get_trace : temps propre par union des enfants »

## Arbre de trace compact
- Alternative : un span par ligne (125 lignes pour une commande, 5 000 caractères)
- Raison : sorties courtes. Trois règles : les spans identiques côte à côte sont regroupés (×3) ; un span qui ne fait que passer la main à un seul enfant du même service tient sur la même ligne ; les spans de moins de 5 % de la durée totale sont cachés, sauf en erreur. Un appel caché vers un autre service reste signalé (« calls shipping 9 ms ») pour que l'agent ne perde jamais un passage d'un service à l'autre. Une commande passe à environ 1 000 caractères

## Test automatique : tous les outils sont exposés
- Alternative : vérifier à la main
- Raison : j'ai déjà perdu un décorateur @mcp.tool() en modifiant server.py. La fonction marchait quand on l'appelait directement, mais l'agent ne la voyait plus. Le test démarre le serveur comme un vrai client MCP et compare la liste exacte des outils. Vérifié : il échoue si on retire un décorateur

## LLM pour le développement : Gemini, offre gratuite
- Alternatives : Groq, Mistral
- Raison : Groq a un plafond de tokens par minute trop bas pour un agent, qui renvoie tout l'historique de la conversation à chaque tour. Gemini gratuit suffit pour développer. Le modèle payant à version figée, qui servira pour les chiffres, sera choisi à l'étape 3, une fois le coût d'un diagnostic mesuré

## Modèle de développement : gemini-3.5-flash-lite (et pas gemini-3.6-flash)
- Alternatives : gemini-3.6-flash (premier choix), attendre le quota du lendemain, activer la facturation
- Raison : gemini-3.6-flash, le plus récent qui appelait bien un outil de test, n'a que 20 requêtes par jour en gratuit. Un diagnostic en demande environ 16 (un appel au LLM par tour), donc même pas un passage complet par jour : le premier essai a été coupé au moment de conclure. gemini-3.5-flash-lite a 15 requêtes par minute et 500 par jour. Il ne fait pas de raisonnement caché (0 token de raisonnement, contre 462 sur 500 pour 3.6-flash sur une question simple), ce qui le rend moins cher mais peut-être moins prudent. (3.7 et 3.8 répondaient « surchargé », la série 4 a un quota gratuit à 0)

## Limites de débit gérées par l'agent, attentes notées à part
- Alternative : laisser la bibliothèque google-genai réessayer seule
- Raison : elle réessaie les 429 et 503 en silence, avec des attentes de plus en plus longues. Un dépassement du quota du jour ressemblait à un agent bloqué. Maintenant elle ne fait qu'une tentative et c'est l'agent qui décide : limite par minute ou surcharge (503), il attend le délai indiqué par Google (ou 20 s) et réessaie 3 fois au plus ; limite par jour, il s'arrête tout de suite avec un message clair. Chaque attente est notée dans le journal, et la durée est donnée avec et sans attentes, pour que le banc mesure le temps réel de l'agent

## Graphe de l'agent écrit à la main, pas create_agent
- Alternative : `create_agent` de LangChain, qui fait la même boucle en une ligne
- Raison : deux nœuds (llm, tools) et une arête conditionnelle, ça se lit en entier. Il faut aussi la main sur la boucle pour la limite de 15 appels d'outils (au-delà, le modèle est appelé avec `tool_choice="none"` et doit conclure) et pour la pause de validation avant une action, à l'étape 4

## Prompt système sans nom de l'application
- Alternative : dire au LLM quelle démo il surveille
- Raison : le test de renommage mesure si l'agent reconnaît la démo au lieu de diagnostiquer. Si le prompt la nomme, le test ne veut plus rien dire. Le prompt ne parle pas non plus d'injection, pour ne pas mettre l'agent en alerte et fausser la mesure. Il demande une vue d'ensemble d'abord et de ne creuser que les services anormaux : sans ça, le premier essai a vérifié les services sains un par un jusqu'à épuiser son budget

## Journal de chaque passage dans runs/, sorties d'outils en entier
- Alternative : ne garder que la taille des sorties
- Raison : pour étudier l'injection, il faut le texte exact que le LLM a lu. Sans lui, je n'ai pas pu relire ce qu'avait renvoyé search_logs(flagd) au premier essai. Les outils limitent déjà leur sortie (20 groupes de 300 caractères pour les logs, environ 1 000 pour une trace), donc le fichier reste léger. Le journal donne aussi les tokens par tour (entrée, sortie, raisonnement), les totaux et la durée : il sert au banc et au suivi des coûts. runs/ est hors du dépôt

## Fuites des noms de flags
- Question : l'agent peut-il lire le nom de la panne activée au lieu de la diagnostiquer ?
- Constat, démo au repos (tous les flags à off), en cherchant les 20 noms dans les sorties réelles des 6 outils :

| Source | Où le nom existe | Sort par nos outils ? |
|---|---|---|
| Logs | événements `shipping.feature_flag.evaluated`, attribut `feature_flag_key` | Non : search_logs ne renvoie que le texte, la sévérité, le service et `exception.message`. Et ces événements ont `@timestamp` à 1970, donc le filtre de temps les écarte |
| Spans | attributs `feature_flag.key`, événements `feature_flag.evaluation` | Non : les outils de traces ne lisent ni les attributs (sauf l'URL) ni les événements (sauf `exception`) |
| URL des spans | `/ofrep/v1/evaluate/flags/loadGeneratorFloodHomepage` (le générateur de charge interroge flagd) | Possible en théorie : find_traces met l'URL du span racine dans `root`. Jamais vu en pratique, ces traces de 30 ms ne sont pas parmi les plus lentes |
| list_services, get_container_status | aucun | Non. Seul le service `flagd` (et la route `/flagservice/`) montre qu'un service de flags existe |

- Rien ne fuit au repos. Mais quatre pannes de la démo écrivent leur propre nom dans les logs, et ce texte passe par search_logs : kafkaQueueProblems (checkout et fraud-detection), kafkaConsumerDead (fraud-detection), adManualGc (ad, en warn), productCatalogFailure (« Product Catalog Fail Feature Flag Enabled », aussi dans les erreurs des traces)
- Conséquence : ces scénarios seront soit masqués, soit exclus, soit comptés à part. Décision prise à l'étape 3
- Point ouvert : le filtre sur `@timestamp` cache aussi tout vrai log sans date. Je n'y touche pas, le corriger rendrait visibles les événements `feature_flag.evaluated`, donc les noms de flags

## Modèle choisi par la config avec init_chat_model, sans LiteLLM
- Alternative : LiteLLM (l'idée reprise de l'agent de la démo, voir « Serveur MCP »)
- Raison : une couche de moins, même résultat. Le nom du modèle est dans AGENT_MODEL (.env) et `init_chat_model` de LangChain charge le bon fournisseur. Le fournisseur est donné en clair (`google_genai`), sinon LangChain peut deviner Vertex AI à cause du préfixe « gemini »

## Pont MCP écrit à la main (pas langchain-mcp-adapters)
- Alternatives : langchain-mcp-adapters, ou repasser mcp en 1.x
- Raison : langchain-mcp-adapters 0.3.1 demande `mcp>=1.24.0` sans limite haute, donc il s'installe à côté de mcp 2.3.0, mais il plante dès l'import : il cherche `RequestContext` dans `mcp.shared.context`, qui n'existe plus en v2. Je ne rétrograde pas mcp (voir « SDK MCP officiel, version 2 »). langchain 1.5.0, langgraph 1.2.20 et langchain-google-genai 4.5.0 s'installent sans problème avec mcp 2.3.0, et les tests passent toujours. À revérifier quand une version de l'adaptateur compatible v2 sortira

## get_trace : temps propre par union des enfants
- Alternative : garder l'ancien calcul, fait en remontant le chemin critique
- Raison : sur une vraie commande ratée, l'outil donnait 15 s de temps propre au frontend, qui ne faisait qu'attendre checkout. L'ancien calcul coupait chaque enfant à la fin de son parent, mais le comparait à une limite venue du grand-parent : avec 1 ou 2 ms de décalage d'horloge entre services, l'enfant était écarté comme « parallèle » et toute sa durée comptée au parent. Maintenant, le temps propre d'un span = sa durée moins l'union des intervalles de ses enfants, chacun coupé à la fenêtre du parent. Ça ne dépend plus du chemin. Le chemin critique part de la fin de chaque span et suit l'enfant qui finit le plus tard. Un enfant n'est écarté que s'il commence après le point atteint : dans une autre trace, trois appels démarraient 0,2 ms avant la fin de PlaceOrder, et l'ancienne règle (écarter s'il finit après) faisait sortir les 13 s de checkout du chemin. Testé sur une fausse trace qui reproduit les deux cas, puis revérifié avec intlShippingSlowdown : shipping /ship-order reste le coupable avec 5 s de temps propre, tout le reste sous 15 ms

## get_trace : appelé qui continue après l'abandon de l'appelant
- Raison : quand le frontend abandonne au bout de 15 s et que checkout continue jusqu'à 17 s, l'arbre l'écrit sur la ligne de l'appelé : « caller gave up after 15.0 s, callee ran 17.1 s ». C'est une information utile pour le diagnostic (un timeout côté appelant). Seuil : l'appelé finit plus de 5 % de la durée du parent après lui. En dessous, c'est du décalage d'horloge et on n'écrit rien

## get_trace : la cible de l'appel fait partie du regroupement
- Alternative : regrouper par service et nom d'opération seulement
- Raison : checkout fait deux « POST », vers shipping et vers email. Ils étaient fusionnés en « POST ×2 [calls shipping] », et l'erreur de l'appel vers email semblait venir de shipping. La cible est lue dans les attributs du span client (peer.service, server.address, net.peer.name, sinon l'hôte de l'URL) et affichée : « POST (to email) ». Quand ce n'est qu'une adresse IP (cas des appels gRPC vers cart), on prend le service de l'enfant s'il est dans la trace

## Lenteur de checkout au repos : manque de mémoire
- Constat : au repos, checkout avait un p95 de 12 s en permanence, et quelques commandes échouaient sur un timeout vers email. Le temps se perdait dans checkout lui-même (code entre les appels, et 6 à 7 s par commande entre l'envoi d'un appel et le traitement de la réponse), alors que les services appelés répondaient en quelques ms
- Cause : la limite de la démo, 20M, alors que checkout utilise environ 19 Mo une fois à l'aise. Le binaire fait environ 14 Mo, et ses pages comptent dans la même limite que la mémoire de travail. Linux les retirait sans arrêt de la mémoire, puis les relisait sur le disque au prochain passage dans ce code. Email n'y était pour rien : il répondait en 10 ms, mais checkout dépassait son propre délai pendant qu'il attendait le disque
- Preuves : 1,64 To lus en 2 h 20 (121 MiB/s en continu), 39 millions de défauts de page majeurs, 10 Mo en swap, processus bloqué environ 5 % du temps par manque de mémoire (PSI)
- Remède : limite à 100M, comme pour le catalogue. 10 min après : 0 KiB/s lu (10 MiB en tout), 57 défauts de page, p95 de 92 ms au lieu de 12 s, commande la plus lente en 235 ms, 0 timeout vers email
- Conséquence : les deux premiers tests « au repos » ont tourné sur une démo vraiment lente. L'agent avait raison de signaler checkout ; c'est l'attendu du test qui était faux

## Fichier compose à moi et scripts de démarrage
- Alternatives : modifier compose.yaml de la démo, ou son fichier compose.extras.yaml (prévu pour ça)
- Raison : demo/compose.local.yaml est dans mon dépôt, donc versionné avec le projet, et la démo reste identique à l'original (git status propre). Compose le charge en dernier et fusionne seulement les clés qu'il contient. scripts/start_demo.sh lance `make start` en ajoutant ce fichier par la variable DOCKER_COMPOSE_FILES_EXTRAS du Makefile ; stop_demo.sh fait pareil pour `make stop`. Vérifié : après démarrage, `docker inspect` donne bien 100M pour checkout et le catalogue. Attention, `make stop` efface les volumes (données d'OpenSearch comprises), c'est le comportement de la démo

## Scénarios de mise au point et scénarios de mesure
- Règle : les scénarios servent soit à mettre l'agent au point, soit à le mesurer, jamais les deux. On ne règle jamais le prompt sur un scénario de mesure
- Raison : si je retouche le prompt jusqu'à ce qu'un test passe, l'agent est adapté à ce test et le chiffre ne dit plus rien de sa capacité à diagnostiquer. Exemple : le test au repos a montré une conclusion trop forte, et le problème venait en fait d'une démo lente et d'un outil qui accusait le mauvais span, pas du prompt. Les corrections vont dans les outils et l'environnement, le prompt reste fixe pendant la mesure

## Rapport JSON en fin de boucle (étape 2.3)
- Alternatives : lire la réponse libre de l'agent à la main, ou lui demander de finir par du JSON dans le texte
- Raison : pour compter des résultats sur beaucoup de runs, il faut des champs fixes (état, service coupable, type de panne, confiance). Un nœud « report » vient après la boucle : il reçoit tout l'historique et remplit le modèle Pydantic `Report` (agent/report.py) par `with_structured_output`. La bibliothèque valide le JSON, donc un champ hors liste ou une confiance à 1,5 est refusé. Les chiffres d'impact sont des nombres (taux d'erreur, latence, nombre d'occurrences, fenêtre), pas du texte, pour pouvoir les comparer

## Liste fermée des types de panne
- Liste : application_error, latency, crash_or_restart, resource_limit, bad_config, bad_deployment, dependency_failure, unknown
- Raison : elle suit les pannes prévues au plan, pour qu'on puisse comparer directement le type annoncé au type injecté. « unknown » laisse au modèle une sortie honnête. Limite vue au premier run : quand il n'y a pas de panne, le modèle met « unknown » faute de mieux. À l'analyse, on ne lit fault_type que si status = incident

## Appels d'outils numérotés pour les preuves
- Alternative : numéroter les sorties dès la boucle d'enquête
- Raison : chaque appel d'outil reçoit un numéro global dans le journal (`call`). Seul le nœud report voit une copie de l'historique où chaque sortie commence par « [tool call #N] ». La boucle d'enquête voit donc exactement la même chose qu'avant, et les runs restent comparables avec les précédents

## Vérifications du rapport après coup, sans correction
- Raison : on mesure ce que le modèle a écrit, on ne le répare pas. Après le run, on vérifie deux choses et on écrit le résultat dans le journal (`checks`) :
  1. root_cause_service est dans la liste de list_services, recalculée sur 60 min juste après la boucle ;
  2. pour chaque preuve, l'appel cité existe et l'extrait figure bien dans sa sortie.
- La comparaison ignore les différences d'espaces, parce que le modèle recopie souvent du JSON indenté sur une seule ligne, ou l'inverse. Elle ne tolère rien d'autre : un extrait reformulé compte comme faux. Le résultat donne le nombre de preuves vérifiées sur le total. C'est un premier indicateur pour l'étude de l'injection : une preuve inventée, ou citée depuis un log piégé, se verra ici
- Limite : list_services ne connaît que les services qui produisent des traces. Un coupable comme kafka ou postgres serait marqué « pas dans list_services » alors qu'il existe. À revoir quand un scénario le demandera
