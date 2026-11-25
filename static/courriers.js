"use strict";
/* Courriers types générés à partir d'un dossier sinistre.
 * Chaque modèle produit : destinataire, objet, corps (HTML). Les informations
 * manquantes apparaissent surlignées « [à compléter : …] ». Le courrier reste
 * entièrement modifiable avant impression ou copie. */

const dateLongue = (iso) =>
  iso ? new Date(iso.slice(0, 10) + "T12:00:00").toLocaleDateString("fr-FR", { day: "numeric", month: "long", year: "numeric" }) : "";

function courrierHelpers(s, c) {
  const blank = (label) => `<mark>[à compléter : ${esc(label)}]</mark>`;
  const v = (val, label) => (val ? esc(val) : blank(label));
  const d = (iso, label) => (iso ? dateLongue(iso) : blank(label));
  const br = (txt) => esc(txt).replace(/\n/g, "<br>");
  const assureur = s.assureur || c.assureur;
  const residence = c.nom ? `la copropriété ${esc(c.nom)}${c.adresse ? `, sise ${esc(c.adresse)}` : ""}` : blank("copropriété");
  return { blank, v, d, br, assureur, residence };
}

const MODELES = [
  {
    id: "declaration",
    titre: "Déclaration de sinistre à l'assureur",
    destinataire: (s, c, h) => `${h.v(h.assureur, "assureur")}<br>${c.assureur_adresse ? h.br(c.assureur_adresse) : h.blank("adresse de l'assureur")}`,
    email: (s, c) => c.assureur_email,
    objet: (s) => `Déclaration de sinistre — ${esc(s.type)}`,
    corps: (s, c, h) => `
      <p>Madame, Monsieur,</p>
      <p>En notre qualité de syndic de ${h.residence}, assurée auprès de votre compagnie au titre du contrat
      multirisque immeuble n° ${h.v(c.numero_police, "n° de police")}, nous vous déclarons le sinistre suivant :</p>
      <ul>
        <li><strong>Nature :</strong> ${esc(s.type)}</li>
        <li><strong>Date de survenance :</strong> ${h.d(s.date_survenance, "date de survenance")}${s.date_constat && s.date_constat !== s.date_survenance ? ` (constaté le ${dateLongue(s.date_constat)})` : ""}</li>
        <li><strong>Localisation :</strong> ${h.v(s.localisation, "localisation")}${s.numero_lot ? ` (lot n° ${esc(s.numero_lot)})` : ""} — parties ${esc((s.parties || "communes").toLowerCase())}</li>
        <li><strong>Circonstances :</strong> ${s.description ? h.br(s.description) : h.blank("circonstances")}</li>
        ${s.tiers ? `<li><strong>Origine / responsable présumé :</strong> ${esc(s.tiers)}</li>` : ""}
        ${s.montant_estime != null ? `<li><strong>Estimation provisoire des dommages :</strong> ${fmtMoney(s.montant_estime)} HT</li>` : ""}
      </ul>
      ${s.convention && s.convention !== "Autre" ? `<p>Ce sinistre nous paraît relever de la convention : ${esc(s.convention)}.</p>` : ""}
      <p>Nous vous remercions de bien vouloir enregistrer cette déclaration, de nous communiquer la référence
      de votre dossier et, le cas échéant, de mandater un expert. Nous tenons à votre disposition tout
      document complémentaire utile à l'instruction du dossier.</p>
      <p>Nous vous prions d'agréer, Madame, Monsieur, l'expression de nos salutations distinguées.</p>`,
  },
  {
    id: "relance_assureur",
    titre: "Relance de l'assureur",
    destinataire: (s, c, h) => `${h.v(h.assureur, "assureur")}<br>${c.assureur_adresse ? h.br(c.assureur_adresse) : h.blank("adresse de l'assureur")}`,
    email: (s, c) => c.assureur_email,
    objet: (s, c, h) => `Relance — sinistre n° ${h.v(s.numero_sinistre_assureur, "n° de sinistre assureur")}`,
    corps: (s, c, h) => `
      <p>Madame, Monsieur,</p>
      <p>Le ${h.d(s.date_declaration, "date de déclaration")}, nous vous avons déclaré le sinistre
      « ${esc(s.type)} » survenu le ${h.d(s.date_survenance, "date de survenance")} au sein de ${h.residence}
      (contrat n° ${h.v(c.numero_police, "n° de police")}).</p>
      <p>À ce jour, et sauf erreur de notre part, ce dossier n'a pas connu d'évolution. Nous vous remercions de
      bien vouloir nous faire connaître dans les meilleurs délais :</p>
      <ul>
        <li>votre position quant à la prise en charge du sinistre ;</li>
        ${s.expert ? `<li>la date de remise du rapport de l'expert (${esc(s.expert)}) ;</li>` : "<li>les coordonnées de l'expert mandaté et la date de son intervention ;</li>"}
        <li>la date prévisionnelle de règlement de l'indemnité.</li>
      </ul>
      <p>Les copropriétaires concernés attendant la remise en état des lieux, nous vous serions reconnaissants
      de traiter ce dossier avec diligence.</p>
      <p>Nous vous prions d'agréer, Madame, Monsieur, l'expression de nos salutations distinguées.</p>`,
  },
  {
    id: "expert",
    titre: "Courrier à l'expert",
    destinataire: (s, c, h) => `${h.v(s.expert, "expert / cabinet d'expertise")}<br>${h.blank("adresse de l'expert")}`,
    objet: (s, c, h) => `Sinistre n° ${h.v(s.numero_sinistre_assureur, "n° de sinistre assureur")} — ${esc(c.nom || "")}`,
    corps: (s, c, h) => `
      <p>Madame, Monsieur,</p>
      <p>Vous avez été mandaté(e) par ${h.v(h.assureur, "assureur")} dans le cadre du sinistre « ${esc(s.type)} »
      survenu le ${h.d(s.date_survenance, "date de survenance")} au sein de ${h.residence}
      (${h.v(s.localisation, "localisation")}).</p>
      ${
        s.date_expertise
          ? `<p>Suite à votre intervention du ${dateLongue(s.date_expertise)}, nous vous remercions de bien vouloir
             nous transmettre votre rapport d'expertise ainsi que l'état des dommages retenus, afin que nous
             puissions engager les travaux de remise en état.</p>`
          : `<p>Nous vous remercions de bien vouloir nous proposer une date de rendez-vous afin de procéder à
             l'expertise. L'accès aux lieux pourra être organisé avec ${s.declarant ? esc(s.declarant) : "le conseil syndical"}${s.declarant_contact ? ` (${esc(s.declarant_contact)})` : ""}.</p>`
      }
      ${s.montant_devis != null ? `<p>Pour information, le devis de remise en état s'élève à ${fmtMoney(s.montant_devis)}${s.entreprise ? ` (entreprise ${esc(s.entreprise)})` : ""}.</p>` : ""}
      <p>Nous vous prions d'agréer, Madame, Monsieur, l'expression de nos salutations distinguées.</p>`,
  },
  {
    id: "declarant",
    titre: "Information au déclarant / copropriétaire",
    destinataire: (s, c, h) => `${h.v(s.declarant, "nom du déclarant")}<br>${h.blank("adresse")}`,
    email: (s) => (s.declarant_contact || "").match(/[^\s,;]+@[^\s,;]+/)?.[0] || "",
    objet: () => "Votre déclaration de sinistre",
    corps: (s, c, h) => `
      <p>Madame, Monsieur,</p>
      <p>Nous accusons réception de votre signalement concernant le sinistre « ${esc(s.type)} » survenu le
      ${h.d(s.date_survenance, "date de survenance")} au sein de ${h.residence} (${h.v(s.localisation, "localisation")}).</p>
      ${
        s.date_declaration
          ? `<p>Ce sinistre a été déclaré le ${dateLongue(s.date_declaration)} à l'assureur de l'immeuble,
             ${h.v(h.assureur, "assureur")}${s.numero_sinistre_assureur ? `, sous la référence ${esc(s.numero_sinistre_assureur)}` : ""}.</p>`
          : `<p>Nous procédons à sa déclaration auprès de l'assureur de l'immeuble, ${h.v(h.assureur, "assureur")}.</p>`
      }
      <p>Afin de compléter le dossier, nous vous remercions de bien vouloir nous transmettre, si ce n'est déjà fait :</p>
      <ul>
        <li>des photographies des dommages ;</li>
        ${s.type === "Dégât des eaux" ? "<li>le constat amiable dégât des eaux, complété et signé par les parties concernées ;</li>" : ""}
        <li>les coordonnées de votre propre assureur (multirisque habitation) et votre numéro de contrat ;</li>
        <li>le cas échéant, les devis ou factures relatifs aux dommages subis dans vos parties privatives.</li>
      </ul>
      <p>Nous ne manquerons pas de vous tenir informé(e) de l'évolution du dossier, référencé chez nous
      sous le numéro ${esc(s.reference)}.</p>
      <p>Nous vous prions d'agréer, Madame, Monsieur, l'expression de nos salutations distinguées.</p>`,
  },
  {
    id: "devis",
    titre: "Demande de devis à une entreprise",
    destinataire: (s, c, h) => `${h.v(s.entreprise, "entreprise")}<br>${h.blank("adresse de l'entreprise")}`,
    objet: (s, c) => `Demande de devis — ${esc(c.nom || "")}`,
    corps: (s, c, h) => `
      <p>Madame, Monsieur,</p>
      <p>Suite au sinistre « ${esc(s.type)} » survenu au sein de ${h.residence}, nous vous remercions de bien
      vouloir établir un devis détaillé pour la remise en état suivante :</p>
      <ul>
        <li><strong>Localisation :</strong> ${h.v(s.localisation, "localisation")}</li>
        <li><strong>Désordres constatés :</strong> ${s.description ? h.br(s.description) : h.blank("désordres")}</li>
      </ul>
      <p>Pour l'accès aux lieux, vous pouvez prendre contact avec ${h.v(s.declarant, "contact sur place")}${s.declarant_contact ? ` (${esc(s.declarant_contact)})` : ""}.</p>
      <p>Merci de rappeler sur votre devis notre référence ${esc(s.reference)}. Nous attirons votre attention sur
      le fait qu'aucune intervention ne pourra être engagée sans l'accord écrit préalable du syndic.</p>
      <p>Nous vous prions d'agréer, Madame, Monsieur, l'expression de nos salutations distinguées.</p>`,
  },
  {
    id: "tiers",
    titre: "Mise en cause du responsable présumé",
    destinataire: (s, c, h) => `${h.v(s.tiers, "responsable présumé")}<br>${h.blank("adresse")}`,
    objet: (s) => `Sinistre ${esc(s.type.toLowerCase())} — demande de déclaration à votre assureur`,
    corps: (s, c, h) => `
      <p>Madame, Monsieur,</p>
      <p>Le ${h.d(s.date_survenance, "date de survenance")}, un sinistre « ${esc(s.type)} » a endommagé
      ${s.parties === "Privatives" ? "des parties privatives" : "les parties communes"} de ${h.residence}
      (${h.v(s.localisation, "localisation")}).</p>
      <p>Selon les premiers éléments recueillis, ce sinistre trouverait son origine ${h.blank("origine : ex. dans votre lot, fuite d'un équipement…")}.</p>
      <p>Nous vous remercions en conséquence de bien vouloir déclarer ce sinistre à votre assureur responsabilité
      civile dans les meilleurs délais, et de nous communiquer en retour ses coordonnées ainsi que votre numéro
      de contrat${s.type === "Dégât des eaux" ? ", et de nous retourner le constat amiable dégât des eaux complété et signé" : ""}.</p>
      <p>Le cas échéant, nous vous remercions de faire procéder sans délai aux réparations nécessaires afin
      de faire cesser la cause des désordres.</p>
      <p>Nous vous prions d'agréer, Madame, Monsieur, l'expression de nos salutations distinguées.</p>`,
  },
];

async function viewCourrier(id, modeleId) {
  const [s, copros, params] = await Promise.all([api(`/sinistres/${id}`), api("/coproprietes"), api("/parametres")]);
  const c = copros.find((x) => x.id === s.copropriete_id) || {};
  const modele = MODELES.find((m) => m.id === modeleId) || MODELES[0];
  const h = courrierHelpers(s, c);
  const p = params;

  $app.innerHTML = `
    <div class="page-head no-print">
      <h1>Courrier — ${esc(s.reference)}</h1>
      <div class="actions"><a class="btn" href="#/sinistres/${s.id}">← Retour au dossier</a></div>
    </div>
    <div class="courrier-layout">
      <aside class="card no-print courrier-side">
        <h2>Modèle</h2>
        <ul class="modeles">${MODELES.map(
          (m) => `<li><a href="#/sinistres/${s.id}/courrier/${m.id}" class="${m.id === modele.id ? "active" : ""}">${esc(m.titre)}</a></li>`
        ).join("")}</ul>
        <h2>Options</h2>
        <label class="check"><input type="checkbox" id="opt-lrar" ${modele.id === "declaration" || modele.id === "tiers" ? "checked" : ""}> Recommandé avec AR</label>
        ${
          s.documents.length
            ? `<h2>Pièces jointes</h2>${s.documents
                .map((d) => `<label class="check"><input type="checkbox" class="opt-pj" value="${esc(d.nom)}"> ${esc(d.nom)}</label>`)
                .join("")}`
            : ""
        }
        <h2>Actions</h2>
        <div class="actions" style="flex-direction:column;align-items:stretch">
          <button class="btn primary" id="c-print">Imprimer / PDF</button>
          <button class="btn" id="c-copy">Copier le texte</button>
          <button class="btn" id="c-mail">Ouvrir dans ma messagerie</button>
          <button class="btn" id="c-log">Noter l'envoi au journal</button>
          <button class="btn" id="c-reset">Réinitialiser le modèle</button>
        </div>
        <p class="hint">Le courrier est modifiable directement : cliquez dans le texte. Les zones
        <mark>surlignées</mark> sont à compléter.${p.cabinet_nom ? "" : ` <br><strong>Coordonnées du cabinet non renseignées</strong>${isAdmin() ? ' : <a href="#/utilisateurs">les saisir</a>' : " : demandez à un administrateur de les saisir"}.`}</p>
      </aside>
      <div class="sheet-wrap"><article class="sheet" id="sheet" contenteditable="true" spellcheck="true"></article></div>
    </div>`;

  const sheet = document.getElementById("sheet");
  const render = () => {
    const lrar = document.getElementById("opt-lrar").checked;
    const pjs = [...document.querySelectorAll(".opt-pj:checked")].map((x) => x.value);
    sheet.innerHTML = `
      <div class="exp">
        <strong>${p.cabinet_nom ? esc(p.cabinet_nom) : h.blank("nom du cabinet")}</strong><br>
        ${p.cabinet_adresse ? h.br(p.cabinet_adresse) : h.blank("adresse du cabinet")}
        ${p.cabinet_telephone ? `<br>Tél. : ${esc(p.cabinet_telephone)}` : ""}
        ${p.cabinet_email ? `<br>${esc(p.cabinet_email)}` : ""}
      </div>
      <div class="dest">${modele.destinataire(s, c, h)}</div>
      <p class="lieu-date">${p.cabinet_ville ? esc(p.cabinet_ville) : h.blank("ville")}, le ${dateLongue(today())}</p>
      ${lrar ? "<p><strong>Lettre recommandée avec accusé de réception</strong></p>" : ""}
      <div class="refs">
        ${c.nom ? `Copropriété : ${esc(c.nom)}<br>` : ""}
        ${c.numero_police ? `Contrat n° : ${esc(c.numero_police)}<br>` : ""}
        ${s.numero_sinistre_assureur ? `Votre référence : ${esc(s.numero_sinistre_assureur)}<br>` : ""}
        Notre référence : ${esc(s.reference)}
      </div>
      <p class="objet"><strong>Objet : ${modele.objet(s, c, h)}</strong></p>
      ${modele.corps(s, c, h)}
      <div class="signature">${esc(ME.nom)}<br>Pour le syndic${p.cabinet_nom ? `, ${esc(p.cabinet_nom)}` : ""}</div>
      ${pjs.length ? `<p class="pj"><strong>P.J. :</strong><br>${pjs.map((n) => "– " + esc(n)).join("<br>")}</p>` : ""}
      ${p.cabinet_mentions ? `<footer class="mentions">${h.br(p.cabinet_mentions)}</footer>` : ""}`;
  };
  render();
  let modified = false;
  sheet.addEventListener("input", () => (modified = true));
  const rerender = () => {
    if (modified && !confirm("Vos modifications manuelles du courrier seront perdues. Continuer ?")) return false;
    modified = false;
    render();
    return true;
  };
  document.getElementById("opt-lrar").addEventListener("change", (e) => { if (!rerender()) e.target.checked = !e.target.checked; });
  document.querySelectorAll(".opt-pj").forEach((cb) => cb.addEventListener("change", (e) => { if (!rerender()) e.target.checked = !e.target.checked; }));
  document.getElementById("c-reset").onclick = () => { modified = true; rerender(); };
  document.getElementById("c-print").onclick = () => {
    if (sheet.querySelector("mark") && !confirm("Certaines zones sont encore à compléter (surlignées). Imprimer quand même ?")) return;
    window.print();
  };
  const plainBody = () => {
    // Texte du corps seulement (sans en-tête postal) pour un courriel
    const clone = sheet.cloneNode(true);
    clone.querySelectorAll(".exp, .dest, .lieu-date, .mentions").forEach((el) => el.remove());
    clone.style.position = "absolute";
    clone.style.left = "-9999px";
    document.body.appendChild(clone);
    const txt = clone.innerText.replace(/\n{3,}/g, "\n\n").trim();
    clone.remove();
    return txt;
  };
  document.getElementById("c-copy").onclick = async () => {
    try {
      await navigator.clipboard.writeText(sheet.innerText);
      toast("Texte copié dans le presse-papiers.");
    } catch {
      toast("Copie impossible : sélectionnez le texte manuellement.", true);
    }
  };
  document.getElementById("c-mail").onclick = () => {
    const to = (modele.email && modele.email(s, c)) || "";
    const subject = sheet.querySelector(".objet")?.innerText.replace(/^Objet\s*:\s*/, "") || modele.titre;
    location.href = `mailto:${encodeURIComponent(to)}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(plainBody())}`;
  };
  document.getElementById("c-log").onclick = async () => {
    const dest = sheet.querySelector(".dest")?.innerText.split("\n")[0] || "";
    const lrar = document.getElementById("opt-lrar").checked ? " (LRAR)" : "";
    try {
      await api(`/sinistres/${s.id}/journal`, {
        method: "POST",
        body: { type: "Courrier", commentaire: `${modele.titre}${lrar} — destinataire : ${dest}` },
      });
      toast("Envoi noté au journal du dossier.");
    } catch (e) {
      toast(e.message, true);
    }
  };
}
