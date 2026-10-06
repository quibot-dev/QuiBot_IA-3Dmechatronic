# QuiBot_IA-3Dmechatronic

![Quibot](docs/img/quibot.jpg)

QuiBot és un prototip de robot humanoide orientat a la interacció educativa. Integra conversa per veu, intel·ligència artificial, expressions facials, gestos articulats i desplaçament per oferir una interacció més natural amb l'usuari.

Aquest repositori conté la documentació, els fitxers mecànics, el firmware i el programari del projecte.

🌐 **Lloc web del projecte:** [https://quibot.upc.edu/]

---

## 📁 Estructura del Repositori

* **`/hardware`**: Esquemes elèctrics elaborats amb KiCad i models CAD 3D (`.STEP`, `.STL`).
* **`/firmware`**: Codi font per al microcontrolador i instruccions de compilació.
* **`/software`**: Programari Python executat a la Raspberry Pi per gestionar la veu, la conversa i les accions del robot.
* **`/bom`**: Llista detallada de components (*Bill of Materials*), proveïdors i costos estimats.
* **`/docs`**: Guies de muntatge, manuals d'ús, esquemes de connexió i resultats de recerca.

---

## Requisits de programari

* Raspberry Pi OS de 64 bits i Python 3.9 o posterior.
* Arduino IDE 2.x i biblioteca `Servo`.
* KiCad 10 per obrir i modificar els esquemes elèctrics.
* Model català de Vosk i clau de l'API d'OpenAI per al sistema d'interacció per veu.
* Programari CAD compatible amb fitxers `.STEP` i `.STL`.

## Com començar

Per preparar el programari de la Raspberry Pi:

```bash
cd software/quibot
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
cp .env.example .env
```

Editeu `.env` per introduir la clau d'OpenAI i ajustar els dispositius d'àudio, els ports sèrie i la ruta del model Vosk. Després, inicieu Quibot amb:

```bash
python -m robot_poc
```

Per carregar el firmware, obriu `firmware/MotionController/MotionController.ino` amb l'Arduino IDE, seleccioneu la placa i el port corresponents i carregueu el programa.

---

## 📜 Llicència i Condicions d'Ús

Aquest projecte és Open Hardware i utilitza les llicències següents:

* **Hardware (`/hardware`):** [CERN-OHL-P-2.0](https://ohwr.org/cernohl)
* **Firmware i programari (`/firmware`, `/software`):** [MIT License](https://opensource.org/licenses/MIT)
* **Documentació (`/docs`):** [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/deed.ca)

> 📢 **Aviseu-nos si feu servir o adapteu aquest material!**  
> Si teniu pensat replicar el projecte, utilitzar aquest material en la vostra recerca o desenvolupar una versió derivada, us agrairem molt que ens ho feu saber enviant un correu a [quibot@upc.edu](mailto:quibot@upc.edu) o obrint una *Issue* en aquest repositori. Això ens ajuda a fer el seguiment de l'impacte del projecte i a justificar el finançament rebut.
>
> ## ⚠️ Avís Legal, Seguretat i Suport

* **Exempció de responsabilitat:** Aquest repositori i tots els seus materials es proporcionen "tal com són" (*AS IS*), amb fins exclusivament docents i de recerca. Els autors i la Universitat Politècnica de Catalunya (UPC) no assumeixen cap responsabilitat per danys directes, indirectes o derivats de la fabricació, muntatge, manipulació o ús dels dissenys, circuits o codis d'aquest projecte.
* **Seguretat mecatrònica:** El dispositiu conté components elèctrics i elements mecànics en moviment. Cal prendre les precaucions de seguretat adients durant la seva manipulació i alimentació elèctrica.
* **Atenció i suport tècnic:** Aquest projecte es manté exclusivament en el marc de les activitats de recerca del grup RISS. L'equip investigador no assumeix cap compromís d'oferir assistència tècnica, suport personalitzat o resolució de peticions a tercers usuaris.

---

## 👥 Autors, Equip i Agraïments

* **Investigadors Principals:** Marta Tarrés i Toni Dorado — Grup de Recerca RISS, Universitat Politècnica de Catalunya (UPC).
* **Desenvolupament i Integració (TFG/TFM):** Marc LLansó Alonso i Pol Camprubí Trujillo
* **Finançament:** Projecte finançat pel Departament d'Igualtat i Feminismes de la Generalitat de Catalunya (`IFE038/24/000087`).

