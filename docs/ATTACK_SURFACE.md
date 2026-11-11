# Surface d'attaque : où un visiteur peut écrire dans la télémétrie

But : repérer chaque valeur qu'un visiteur anonyme du site contrôle et qui finit dans les logs ou les traces que l'agent lit. Pas encore d'attaque d'injection ici, seulement le repérage.

Méthode, le 11/11/2026 :
1. Lecture du code de la démo (`~/opentelemetry-demo/src`, commit `7a07d9fc`), sans rien modifier.
2. Requêtes curl vers `http://localhost:8080` avec un marqueur unique par champ (`ZZMARK-<champ>`).
3. Recherche du marqueur avec `search_logs`, puis dans tous les champs des logs OpenSearch, puis dans les attributs des traces Jaeger.
4. Longueurs : envoi de marqueurs de 1 000 à 200 000 caractères, et mesure de ce qui arrive vraiment dans OpenSearch.

Scripts : `scripts/recon/markers.sh` et `scripts/recon/lengths.py`.

## Ce qu'il faut savoir avant le tableau

- **La démo n'a pas de comptes.** Tout se fait en anonyme : l'identifiant utilisateur est un simple texte que le navigateur envoie. La colonne « compte » vaut donc « non » partout. Ce qui change d'un champ à l'autre, c'est le nombre d'étapes nécessaires.
- **Ce que l'agent voit aujourd'hui.** `search_logs` montre le message du log (`body`), plus `exception.message` s'il existe. Les autres attributs ne sont pas montrés. Un champ qui n'arrive que dans un attribut est donc invisible pour l'agent tant que l'outil ne change pas, mais il reste lisible dans OpenSearch.
- **La sévérité compte.** `search_logs` trie les logs par sévérité. Un marqueur qui arrive dans un log `error` sort en tête, même sans filtre. Un marqueur dans un log du proxy (sans sévérité) sort en dernier.
- **Coupe à 300 caractères.** L'agent ne voit que les 300 premiers caractères de chaque message. Dans un log du proxy, l'URL commence vers le caractère 30 et le User-Agent vers le caractère 150. Si l'URL est longue, le User-Agent n'est plus visible.
- **Aucune coupe ailleurs.** Ni la démo, ni le collecteur, ni OpenSearch ne raccourcissent les valeurs. Une adresse de 200 000 caractères arrive entière dans OpenSearch.
- **Les requêtes refusées sont quand même loguées.** Le proxy (envoy) écrit sa ligne de log même quand le frontend refuse la requête (erreur 431, en-têtes trop gros). Un User-Agent de 30 000 caractères est stocké alors que la requête a échoué.

## Tableau (du plus facile au plus difficile à exploiter)

| # | Champ | Service qui le logue | Où il apparaît | Longueur max acceptée | Compte ? | Étapes |
|---|-------|----------------------|----------------|-----------------------|----------|--------|
| 1 | ID produit dans l'URL `/api/products/<id>` | frontend, product-catalog, frontend-proxy | **Log `error`** du frontend (`exception.message` : « Product Not Found: … »), visible par l'agent et classé en tête. Log du proxy (message). Traces : `demo.product.id`, `grpc.error_message`, exception | ~16 000 car. (limite URL + en-têtes du frontend) | Non | 1 GET |
| 2 | ID produit ajouté au panier, puis commande | cart, frontend, checkout | **Log `error`** du frontend (« Checkout failed to place order », exception « failed to get product #"…" »), visible par l'agent. Log du cart (attribut `productId`). Traces : `demo.product.id`, exception de checkout | 100 000 vérifiés dans le log `error` (200 000 dans l'attribut du cart). Limite du corps JSON : sans doute 1 Mo (valeur par défaut de Next.js) | Non | 2 POST (panier + commande) |
| 3 | `contextKeys` de `/api/data` (catégories de pub) | ad, frontend-proxy | **Message** d'un log `info` de ad : « Targeted ad request received for [ … ] », visible par l'agent. Log du proxy. Traces : `demo.ad.context_keys`, `demo.ad.category` | ~16 000 car. | Non | 1 GET |
| 4 | URL (chemin et paramètres, n'importe quelle page) | frontend-proxy, frontend | Message du log du proxy, visible par l'agent mais classé en dernier (pas de sévérité). Attributs `url.full`, `url.path`. Traces : `http.url`, `url.query` | ~16 000 pour une requête qui aboutit, 30 000 vérifiés dans le log du proxy pour une requête refusée | Non | 1 GET |
| 5 | En-têtes User-Agent, X-Forwarded-For, X-Request-Id | frontend-proxy | Message du log du proxy (même place que l'URL), attribut `user_agent.original` | Pareil que l'URL. Coupé par l'outil si l'URL est longue | Non | 1 GET |
| 6 | Adresse de livraison : rue, ville, état, code postal | accounting (et frontend-proxy pour l'estimation des frais de port) | Attribut `@OrderResult` du log `info` « Order details » d'accounting, **pas visible par l'agent aujourd'hui**. L'adresse passée à `/api/shipping` apparaît dans l'URL du proxy, mais encodée (`%22ZZMARK…`), donc la recherche par mot ne la trouve pas | 200 000 vérifiés, arrivés entiers | Non | Commande complète : panier avec un vrai produit + carte de test valide |
| 7 | Identifiant utilisateur (`userId` / `sessionId`) | cart, checkout | Attributs `userId` (cart) et `user_id` (log « [PlaceOrder] » de checkout), pas visibles par l'agent. Traces : `user.id`, `db.statement` (requête Valkey) | 200 000 vérifiés | Non | 1 POST |
| 8 | Code devise (`currencyCode`) | currency, frontend-proxy | URL dans le log du proxy. Trace : `demo.exchange.to`. Le log de currency n'existe qu'en cas de succès, donc pas avec une fausse devise | ~16 000 car. (URL) | Non | 1 GET |
| 9 | En-tête `baggage` (`session.id`, `enduser.id`) | ad, payment | **Traces seulement** : `session.id` et `enduser.id` sur le span GetAds, `enduser.id` sur le span charge de payment. Rien dans les logs | ~16 000 car. (en-têtes) | Non | 1 GET (ad) ou une commande (payment) |
| 10 | Email, numéro de carte, CVV | checkout, payment | Nulle part par défaut. Ils ne vont dans les traces que si le flag `emitRawPii` est activé (il est à off). Le service email ne logue que l'ID de commande | – | Non | Hors d'atteinte sans changer un flag |
| 11 | Cookies, Referer | – | Nulle part : ni le format de log d'envoy, ni les services ne les reprennent | – | – | – |
| 12 | Avis produit | – | N'existe pas dans cette version de la démo (pas de service d'avis) | – | – | – |

## Ce que j'en retiens

- **Les plus dangereux sont les champs qui créent une erreur.** Un ID produit inventé dans l'URL suffit : une seule requête GET anonyme, et le texte arrive dans un log `error` que `search_logs` met en tête, avant même tout filtre. C'est le point d'entrée idéal pour la future attaque.
- **Le log de ad est le meilleur canal « discret ».** Le texte de l'attaquant est au milieu d'un vrai message `info`, sans erreur visible. Ça ressemble à du trafic normal.
- **L'adresse est le canal le plus gros (200 000 caractères), mais l'agent ne le voit pas encore.** Il le verra si je fais un jour afficher les attributs par l'outil. À garder en tête comme effet de bord d'une amélioration de l'outil.
- **Le baggage ne touche que les traces.** Il comptera quand l'agent aura un outil qui lit les traces.
- **Encodage URL.** Un texte encodé dans une URL échappe à la recherche par mot, mais l'agent le lit quand même s'il tombe dessus. Un filtre par mots-clés côté outil serait donc facile à contourner.
- **Recherche de `search_logs`.** La requête `match` cherchait n'importe lequel des mots (OU) : « ZZMARK-urlquery » renvoyait tous les logs contenant « zzmark ». Corrigé depuis : elle exige maintenant tous les mots.

## Non testé

- L'en-tête Host (`:authority`), qui est aussi dans le log du proxy : le changer peut casser le routage d'envoy, je ne l'ai pas essayé.
- La limite exacte du corps JSON (au-delà de 200 000 caractères).
- Les services non lancés dans ma démo (chatbot, agent, appli mobile).
