# Somfy RTS Gateway

Pilotez vos volets, stores et portails Somfy RTS depuis Home Assistant —
sans cloud, sans passerelle propriétaire : une carte ESP32, un module
CC1101 et cette intégration.

> Cette intégration a été créée avec l'aide de Mistral AI.

## En bref

- **Volets, stores, rideaux, bannes, portails et portes de garage** RTS :
  position estimée, inclinaison, position favorite (MY).
- **Prises et éclairages RTS**, capteurs **soleil/vent** (Sunis, Eolis, Soliris).
- **Groupes RTS** : plusieurs volets, une seule commande.
- **Télécommandes physiques** : Home Assistant suit leur utilisation.
- Codes tournants persistés : les moteurs ne se désappairent pas.

## Matériel

- 1 × ESP32 ([variantes prises en charge](#variantes-esp32))
- 1 × module CC1101
- 1 × antenne 433 MHz

Le CC1101 fait tout : **émission et réception** sur la vraie fréquence RTS
(**433,42 MHz** — pas 433,92 MHz).

| Broche CC1101 | Rôle |
|---|---|
| GDO0 | émission (TX) |
| GDO2 | réception (RX) |
| SCK / MISO / MOSI / CSN | SPI |

Les broches ESP32 se définissent en **une ligne** du `esphome.yaml` :

```yaml
substitutions:
  board_package: esphome_boards/esp32-s3-zero.yaml   # selon votre carte
```

<details>
<summary><h3 id="variantes-esp32">Variantes ESP32 prises en charge</h3></summary>

| Variante | Fichier | SCK / MISO / MOSI / CSN / GDO0 / GDO2 | Framework |
|---|---|---|---|
| ESP32-S3-Zero Waveshare (défaut) | `esp32-s3-zero.yaml` | 4 / 5 / 6 / 7 / 8 / 1 | Arduino |
| ESP32-S3 DevKitC-1 | `esp32-s3.yaml` | 4 / 5 / 6 / 0 / 3 / 10 | Arduino |
| ESP32 WROOM-32, DevKitC, NodeMCU-32S | `esp32-classic.yaml` | 18 / 19 / 23 / 5 / 16 / 17 | Arduino |
| ESP32-C3 DevKitM-1, SuperMini | `esp32-c3.yaml` | 4 / 5 / 6 / 0 / 3 / 10 | Arduino |
| ESP32-C6 DevKitC-1 | `esp32-c6.yaml` | 18 / 19 / 20 / 21 / 6 / 11 | ESP-IDF |

- Sur le S3-Zero, seuls des pads « sûrs » sont utilisés (GPIO0, GPIO33-37
  et GPIO21 sont exclus). Flash annoncé à 4 Mo : ajuster pour la N8R8.
- Les broches évitent les strapping pins, sauf CSN/GDO0 sur S3/C3 qui
  fonctionnent en pratique. Si la carte refuse de démarrer avec le CC1101,
  déplacez CSN/GDO0 (ex. GPIO10/GPIO7).
- Autre carte : copiez le fichier le plus proche et adaptez `esp32:board`
  et les broches.

</details>

<details>
<summary><h3>Fonctionnement radio et pièges de câblage</h3></summary>

- **RX** : le CC1101 démodule l'OOK et sort le signal baseband sur GDO2,
  capté par un `remote_receiver`.
- **TX** : le `remote_transmitter` module GDO0 (RMT) ; le CC1101 en mode
  « async serial » génère la porteuse.
- **Ne déclarez pas `gdo0_pin` sur le composant `cc1101`** (ESP32, OOK) :
  son appel à `pin_mode()` re-route le pad et coupe la liaison RMT → GDO0.
  Résultat : émission muette, sans aucune erreur (esphome/esphome#16876).
- Ne reliez pas GDO0 et GDO2 entre eux.

</details>

## Installation

1. Copiez `custom_components/somfy_rts` dans `config/custom_components/`.
2. Flashez l'ESP32 avec `esphome.yaml` — gardez `esphome_components/` à
   côté du YAML (composant local de décodage RTS). Copiez
   `secrets.yaml.example` en `secrets.yaml` et renseignez
   `wifi_ssid`, `wifi_password`, `api_key` (le fichier réel est ignoré
   par git ; les mises à jour OTA sont protégées par la clé API).
3. Une fois la passerelle connectée, son entité `radio_frequency.*`
   apparaît dans Home Assistant (périphérique « RTS Gateway »).
4. Paramètres → Appareils et services → Ajouter une intégration →
   **Somfy RTS Gateway**.
5. Sélectionnez le transmetteur, puis ajoutez vos appareils.

<details>
<summary><h3>Appairer un appareil</h3></summary>

L'appairage suit la procédure Somfy, guidée par l'assistant :

1. Maintenez **PROG** sur une télécommande physique appairée au moteur,
   jusqu'à son petit va-et-vient (mémoire ouverte).
2. Laissez l'assistant envoyer la commande d'appairage.

Appairage entièrement depuis Home Assistant : services
`enter_programming_mode` puis `send_prog` à la suite.

En cas de renommage de la passerelle, réparez l'entrée via
Modifier → Changer l'émetteur RF.

</details>

<details>
<summary><h3>Appareils pris en charge</h3></summary>

| Plateforme HA | Appareils RTS |
|---|---|
| `cover` | Volets roulants et volets, stores vénitiens (inclinaison 56 et 80 bits), rideaux (ouverture G/D/centre), bannes, portes de garage (1/3 boutons), portails (G/D/centre, 1 bouton) |
| `switch` | Prises On/Off RTS (2401091, 2401093, 2401363), récepteurs de contact |
| `light` | Prise variateur 200 W (2401092), récepteurs éclairage RTS (1811251, 1822215), gammes pilotables par Modulis |
| `binary_sensor` | Capteurs soleil/vent Sunis, Eolis, Soliris (alerte vent + alerte soleil) |

Non couverts, volontairement : contacts secs sur relais GPIO, moteurs
**io-homecontrol**, émission **RTW/RTV** (433,92 MHz).

</details>

### Via HACS

Le dépôt peut être ajouté comme dépôt personnalisé HACS :

1. HACS → ⋮ → *Dépôts personnalisés*.
2. Ajoutez l'URL du dépôt, catégorie **Intégration**.
3. Téléchargez « Somfy RTS Gateway », puis redémarrez Home
   Assistant.

## Utiliser vos volets

Chaque volet expose une entité `cover` avec position estimée (durées
d'ouverture/fermeture configurables), plus deux boutons d'usage courant :
**Position MY** et **Recalage position**.

### Position MY (favorite)

- `my_position` (défaut 50 %) s'enregistre via le wizard ou le service
  `set_my_position`.
- Le réglage **MY simulé** (défaut) convient aux moteurs sans vraie
  position MY : Home Assistant pilote le volet jusqu'à `my_position`.
  Un appui MY court sur la télécommande physique déclenche le même
  déplacement — l'antenne l'entend, HA conduit le volet.
- Le réglage **MY natif** convient aux moteurs qui mémorisent leur
  favorite : une trame MY suffit, et le bouton *Mémoriser position MY*
  écrit la position courante dans le moteur et dans HA.

### Paramètres modifiables à tout moment

Réglages → Appareils et services → **Somfy RTS Gateway** → Modifier :

- durées d'ouverture/fermeture, inclinaison, pas, commande de bascule ;
- inversion montée/descente (moteur monté à l'envers) ;
- inversion de l'échelle de position (ouvert = 0 %, fermé = 100 %) ;
- méthode d'arrêt : *MY* (défaut) ou *impulsion opposée* (moteurs sans MY).

À l'ajout, seules les options pertinentes pour le type et le protocole
sont proposées.

### Télécommandes physiques

Vous n'avez **pas besoin de connaître l'adresse** de vos télécommandes :
dans Somfy RTS Gateway → Modifier, l'entrée *Ajouter une télécommande*
détecte les adresses vues à l'antenne. Appuyez sur un bouton, rafraîchissez,
sélectionnez. Les adresses liées apparaissent dans l'attribut
`linked_remote_addresses` et peuvent aussi se saisir à la main :

```yaml
service: somfy_rts.link_remote
data:
  entity_id: cover.chambre
  remote_address: "01234567"
```

L'adresse d'une télécommande s'observe aussi dans les logs ESPHome
(`RTS frame: address=...`) ou l'événement `somfy_rts_frame`.

### Dérive de position

Les moteurs RTS n'ont pas de retour de position : l'estimation dérive
lentement. `recalibrate` (ou le bouton *Recalage position*) lance un cycle
fermeture → ouverture → retour à la position initiale, ré-ancré sur les
butées mécaniques. À programmer de préférence en automatisation nocturne.

### Capteurs soleil/vent

Déclarez-les depuis le menu d'ajout : chaque capteur expose deux
`binary_sensor` (vent, soleil) et met à jour les attributs
`wind_alarm`/`sun_alarm` des volets liés (`link_remote` avec l'adresse
du capteur). Si le moteur réagit seul à une alerte, recalez la position
avec `set_position`.

## Groupes RTS

Un groupe n'est pas un appareil, c'est un regroupement : il se crée depuis
l'entrée dédiée **« Ajouter un groupe RTS »** (menu d'installation et de
reconfiguration). Le wizard est minimal — nom, type, adresse générée dans
la plage réservée (1 000 000 – 2 097 151) — puis enchaîne sur le choix des
membres.

Chaque volet coché ou décoché passe par la séquence guidée Somfy :

1. La passerelle maintient l'appui PROG long : le moteur fait un **petit
   va-et-vient** (mémoire ouverte). Confirmez — ou ouvrez la mémoire
   vous-même avec une télécommande physique si le moteur ne réagit pas.
2. L'appui PROG du groupe est envoyé : le moteur réagit à nouveau
   (appairage confirmé). Confirmez.

L'appartenance (`group_addresses` sur chaque volet, `members` sur le
groupe) n'est enregistrée qu'après confirmation. La suppression d'un
groupe suit le même principe depuis **« Supprimer un groupe RTS »** :
désappairage guidé de chaque membre (optionnel), puis retrait du groupe.

Commander le groupe déplace aussi les entités membres : leur
estimation suit le mouvement (à leur vitesse propre, sans radio
supplémentaire), y compris position cible, MY, inclinaison et recalage.

Les services `add_to_group` / `remove_from_group` envoient la même
séquence sans confirmation — à réserver aux automatisations.

## Services

| Service | Usage |
|---|---|
| `goto_my_position` | rejoindre la position favorite |
| `set_my_position` | enregistrer la position favorite |
| `memorize_my_position` | MY long : le moteur mémorise sa position (MY natif) |
| `link_remote` / `unlink_remote` | lier / détacher une télécommande physique |
| `add_to_group` / `remove_from_group` | appairer / désappairer des volets à un groupe |
| `enter_programming_mode` | PROG long : le moteur entre en mode programmation |
| `send_prog` | PROG court : appaire / désappaire l'adresse de la passerelle |
| `set_position` / `set_tilt_position` | recaler l'estimation sans bouger le moteur |
| `recalibrate` | cycle fermeture → ouverture → retour |
| `send_command` | commande brute RTS (UP, DOWN, MY, STOP, PROG, STEP_*) |
| `send_step_command` | pas avec taille explicite, 1-127 (80 bits) |

Chaque trame décodée émet aussi un événement `somfy_rts_frame` avec
`address`, `command`, `rolling_code`, `step`, `bits`, `device` et
`source` (`remote` ou `gateway`) — de quoi déclencher des automatisations
sur les boutons des télécommandes.

## Retirer l'intégration

1. Paramètres → Appareils et services → Somfy RTS Gateway → les trois
   points → **Supprimer l'entrée**.
2. Pour que les moteurs cessent de répondre à la passerelle :
   désappairez-les avant (`enter_programming_mode` puis `send_prog`, ou
   bouton PROG du moteur).
3. Retirez ensuite le dossier `custom_components/somfy_rts`.

## Inspirations

Ce projet est inspiré de :

- [ESPSomfy-RTS](https://github.com/rstrouse/ESPSomfy-RTS) (rstrouse)
  — types d'ouvrants, modèle d'inclinaison, format 80 bits à molette.
- esphome_somfy (projet communautaire ESPHome) —
  décodage des trames RTS côté ESPHome.
- [Somfy RTS protocol](https://pushstack.wordpress.com/somfy-rts-protocol/)
  (pushstack) — documentation du protocole RTS.

## Développement

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements_test.txt
```

```bash
.venv/Scripts/python.exe -m pytest tests/ -q          # 320 tests
.venv/Scripts/python.exe -m mypy custom_components/somfy_rts   # strict
.venv/Scripts/python.exe -m ruff check tests custom_components/somfy_rts
```

Typage strict (checklist Platinum), couverture ~99 % — configuration dans
`mypy.ini`.

Une CI GitHub Actions tourne à chaque push : tests, typage, lint,
validation des 5 variantes ESPHome et validation HACS (voir
`.github/workflows/`).

<details>
<summary><h3>Architecture</h3></summary>

```
Home Assistant (integration somfy_rts)
   |  SomfyRTSCommand (rf-protocols) via radio_frequency.async_send_command
   v
Entité radio_frequency.somfy_rts_radio (créée par l'intégration esphome)
   |  API native ESPHome (radio_frequency_transmit_raw_timings)
   v
ESP32 : ir_rf_proxy -> remote_transmitter -> GPIO3 == GDO0 (CC1101, TX ASK)
                                                            ──> 433,42 MHz
ESP32 : CC1101 (RX OOK) -> GDO2 -> remote_receiver -> composant somfy_rts
        -> événement HA esphome.somfy_rts_rx -> mise à jour des covers
```

L'encodage des trames (checksum, obfuscation, Manchester, répétitions)
est assuré par la bibliothèque `rf-protocols` de l'intégration native
`radio_frequency`. Les codes tournants sont persistés dans le stockage
Home Assistant (`somfy_rts.rolling_codes`) : ils survivent aux
redémarrages — condition indispensable pour ne pas désappairer les moteurs.

</details>

<details>
<summary><h3>Protocoles et points d'attention</h3></summary>

- **RTS 56 bits** (défaut) : la grande majorité des moteurs Somfy.
- **RTS 80 bits** : télécommandes à molette (Modulis, Situo) et leurs
  moteurs — trame 56 bits + 3 octets d'extension non obfusqués, synchro
  matérielle 12/14 impulsions.
- **Inclinaison** : en 80 bits, commandes molette (Step Up/Down,
  `tilt_step_percent` pour l'estimation, `step_size` pour la trame) ;
  en 56 bits, moteur d'inclinaison temporisé (`tilt_time_ms`). Un
  appui court d'une télécommande physique est lu comme
  montée/descente, un appui long (≥ 15 répétitions) comme inclinaison.
- N'utilisez **qu'une seule source** de commandes par adresse : les codes
  tournants se désynchronisent sinon (remède : ré-appairer le moteur).
- Ne réutilisez pas l'adresse d'une télécommande existante : chaque
  émetteur RTS doit avoir une adresse unique.
- La fréquence RTS est **433,42 MHz** : configurez le CC1101 avec
  `frequency: 433.42MHz`.

</details>

<details>
<summary><h3>Dépannage</h3></summary>

- **Le volet ne répond pas** : vérifiez que l'entité `radio_frequency` de
  la passerelle est disponible ; consultez les logs ESPHome (`somfy_rts`)
  et les diagnostics de l'intégration.
- **L'état ne suit plus la télécommande physique** : vérifiez que RX est
  activé (Réglages généraux) et que l'adresse est liée ; utilisez
  l'assistant *Ajouter une télécommande*.
- **La position dérive** : lancez `recalibrate` (idéalement en
  automatisation nocturne) et vérifiez les durées d'ouverture/fermeture.
- **Une commande ne fait plus rien après usage d'un autre logiciel** :
  codes tournants désynchronisés ; ré-appairez le moteur.
- **Appairage impossible** : la passerelle doit voir la trame PROG dans
  les logs ESPHome ; vérifiez l'antenne et l'alimentation du CC1101.

</details>

## Licence

Distribué sous licence MIT — voir [LICENSE](LICENSE).
