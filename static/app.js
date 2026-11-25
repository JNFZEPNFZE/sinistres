"use strict";

/* ------------------------------------------------------------------ */
/* Utilitaires                                                         */
/* ------------------------------------------------------------------ */
const $app = document.getElementById("app");
let META = null;
let ME = null; // utilisateur connecté
const isAdmin = () => ME && ME.role === "admin";

const CONVENTIONS = [
  "",
  "IRSI tranche 1 (≤ 1 600 € HT)",
  "IRSI tranche 2 (1 600 – 5 000 € HT)",
  "CIDE-COP (> 5 000 € HT)",
  "Droit commun",
  "Autre",
];

// Délais légaux de déclaration à l'assureur (Code des assurances, art. L113-2 / L125-2)
const DELAIS = {
  "Vol / Vandalisme": { jours: 2, ouvres: true, libelle: "2 jours ouvrés" },
  "Catastrophe naturelle": { jours: 30, ouvres: false, libelle: "30 jours après publication de l'arrêté" },
};
const DELAI_DEFAUT = { jours: 5, ouvres: true, libelle: "5 jours ouvrés" };

function esc(v) {
  return String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function fmtDate(iso) {
  if (!iso) return "";
  const [y, m, d] = iso.slice(0, 10).split("-");
  return `${d}/${m}/${y}`;
}
function fmtMoney(v) {
  if (v === null || v === undefined || v === "") return "";
  return Number(v).toLocaleString("fr-FR", { style: "currency", currency: "EUR" });
}
const today = () => new Date().toISOString().slice(0, 10);

function joursOuvres(from, to) {
  let n = 0;
  const d = new Date(from + "T12:00:00");
  const end = new Date(to + "T12:00:00");
  while (d < end) {
    d.setDate(d.getDate() + 1);
    const wd = d.getDay();
    if (wd !== 0 && wd !== 6) n++;
  }
  return n;
}
function joursCalendaires(from, to) {
  return Math.round((new Date(to + "T12:00:00") - new Date(from + "T12:00:00")) / 86400000);
}

function toast(msg, isError = false) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.className = "toast" + (isError ? " error" : "");
  t.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (t.hidden = true), 3200);
}

async function api(path, options = {}) {
  const opts = { ...options, headers: { "Content-Type": "application/json", "X-Requested-With": "fetch" } };
  if (opts.body && typeof opts.body !== "string") opts.body = JSON.stringify(opts.body);
  const res = await fetch("/api" + path, opts);
  const data = await res.json().catch(() => ({}));
  if (res.status === 401 && data.auth && ME) {
    ME = null;
    boot();
  }
  if (!res.ok) throw new Error(data.erreur || `Erreur ${res.status}`);
  return data;
}

/* Champs personnalisés : saisie et affichage */
function champInput(def, value) {
  const name = `cp:${def.cle}`;
  const id = `cp-${def.cle}`;
  const val = value ?? "";
  let field;
  if (def.type === "liste")
    field = `<select id="${id}" name="${name}"><option value="">—</option>${def.options
      .map((o) => `<option ${o === val ? "selected" : ""}>${esc(o)}</option>`)
      .join("")}</select>`;
  else if (def.type === "oui_non")
    field = `<select id="${id}" name="${name}"><option value="">—</option><option value="1" ${val === true ? "selected" : ""}>Oui</option><option value="0" ${val === false ? "selected" : ""}>Non</option></select>`;
  else {
    const type = { nombre: "number", date: "date" }[def.type] || "text";
    field = `<input id="${id}" name="${name}" type="${type}" ${type === "number" ? 'step="any"' : ""} value="${esc(val)}">`;
  }
  return `<div><label for="${id}">${esc(def.libelle)}</label>${field}</div>`;
}
function champDisplay(def, v) {
  if (v === undefined || v === null || v === "") return "";
  if (def.type === "oui_non") return v ? "Oui" : "Non";
  if (def.type === "date") return fmtDate(v);
  if (def.type === "nombre") return Number(v).toLocaleString("fr-FR");
  return esc(v);
}
function extractChamps(data, champs) {
  const out = {};
  const types = Object.fromEntries(champs.map((c) => [c.cle, c.type]));
  for (const k of Object.keys(data)) {
    if (!k.startsWith("cp:")) continue;
    const cle = k.slice(3);
    const v = data[k];
    if (v !== "") out[cle] = types[cle] === "oui_non" ? v === "1" : v;
    delete data[k];
  }
  data.champs_perso = out;
  return data;
}
function gestionnaireOptions(users, sel, { all = true } = {}) {
  return `${all ? `<option value="">Tous les gestionnaires</option><option value="moi" ${sel === "moi" ? "selected" : ""}>Mes dossiers</option>` : ""}
    ${users.filter((u) => u.actif || String(u.id) === String(sel)).map((u) => `<option value="${u.id}" ${String(u.id) === String(sel) ? "selected" : ""}>${esc(u.nom)}</option>`).join("")}`;
}
function prefGet(k, def) {
  try { return localStorage.getItem(k) ?? def; } catch { return def; }
}
function prefSet(k, v) {
  try { localStorage.setItem(k, v); } catch { /* stockage indisponible */ }
}

function badge(statut) {
  let cls = "";
  if (statut === "Clôturé") cls = "clos";
  else if (statut === "Refusé / Sans suite") cls = "refuse";
  else if (statut === "À déclarer") cls = "todo";
  return `<span class="badge ${cls}">${esc(statut)}</span>`;
}
const isClos = (s) => META.statuts_clos.includes(s.statut);
const isLate = (s) => !isClos(s) && s.date_echeance && s.date_echeance < today();

/* ------------------------------------------------------------------ */
/* Routeur                                                             */
/* ------------------------------------------------------------------ */
const routes = [
  [/^#?\/?$/, viewDashboard, "dashboard"],
  [/^#\/sinistres$/, viewList, "sinistres"],
  [/^#\/sinistres\/nouveau$/, () => viewForm(null), "sinistres"],
  [/^#\/sinistres\/(\d+)$/, (id) => viewDetail(id), "sinistres"],
  [/^#\/sinistres\/(\d+)\/modifier$/, (id) => viewForm(id), "sinistres"],
  [/^#\/sinistres\/(\d+)\/courrier(?:\/(\w+))?$/, (id, m) => viewCourrier(id, m), "sinistres"],
  [/^#\/coproprietes$/, viewCopros, "coproprietes"],
  [/^#\/compte$/, viewAccount, "compte"],
  [/^#\/utilisateurs$/, viewUsers, "utilisateurs"],
];

async function router() {
  if (!ME) return;
  if (!META) META = await api("/meta");
  const hash = location.hash || "#/";
  for (const [re, fn, nav] of routes) {
    const m = hash.match(re);
    if (m) {
      document.querySelectorAll("[data-nav]").forEach((a) => a.classList.toggle("active", a.dataset.nav === nav));
      try {
        await fn(...m.slice(1));
      } catch (e) {
        $app.innerHTML = `<div class="card"><p class="late">${esc(e.message)}</p></div>`;
      }
      window.scrollTo(0, 0);
      return;
    }
  }
  $app.innerHTML = `<div class="card">Page introuvable. <a href="#/">Retour</a></div>`;
}
window.addEventListener("hashchange", router);

/* ------------------------------------------------------------------ */
/* Connexion / premier lancement                                       */
/* ------------------------------------------------------------------ */
function renderUserMenu() {
  const el = document.getElementById("usermenu");
  document.body.classList.toggle("anon", !ME);
  el.innerHTML = ME
    ? `${isAdmin() ? '<a href="#/utilisateurs" data-nav="utilisateurs">Administration</a>' : ""}
       <a href="#/compte" data-nav="compte" title="Mon compte">${esc(ME.nom)}</a>
       <button class="btn small" id="logout">Déconnexion</button>`
    : "";
  document.getElementById("logout")?.addEventListener("click", async () => {
    await api("/deconnexion", { method: "POST" }).catch(() => {});
    ME = null;
    boot();
  });
}

async function boot() {
  let ses;
  try {
    ses = await api("/session");
  } catch (e) {
    $app.innerHTML = `<div class="card late">Serveur injoignable : ${esc(e.message)}</div>`;
    return;
  }
  ME = ses.utilisateur;
  renderUserMenu();
  if (ME) return router();
  if (ses.installation) return viewSetup();
  viewLogin();
}

function viewLogin() {
  $app.innerHTML = `
    <form id="login" class="card auth-card">
      <h1>Connexion</h1>
      <label for="l-id">Identifiant</label>
      <input id="l-id" name="identifiant" autocomplete="username" required autofocus>
      <label for="l-pw" style="margin-top:.75rem">Mot de passe</label>
      <input id="l-pw" name="mot_de_passe" type="password" autocomplete="current-password" required>
      <div id="l-err" class="late" style="margin-top:.75rem"></div>
      <button class="btn primary" style="margin-top:1rem;width:100%">Se connecter</button>
    </form>`;
  document.getElementById("login").onsubmit = async (e) => {
    e.preventDefault();
    try {
      const r = await api("/connexion", { method: "POST", body: Object.fromEntries(new FormData(e.target)) });
      ME = r.utilisateur;
      renderUserMenu();
      router();
    } catch (err) {
      document.getElementById("l-err").textContent = err.message;
    }
  };
}

function viewSetup() {
  $app.innerHTML = `
    <form id="setup" class="card auth-card">
      <h1>Bienvenue</h1>
      <p class="muted">Créez le compte administrateur. Vous pourrez ensuite ajouter vos collègues depuis « Administration ».</p>
      <div class="form-grid" style="grid-template-columns:1fr">
        <div><label>Nom complet</label><input name="nom" required autofocus></div>
        <div><label>Identifiant de connexion</label><input name="identifiant" required autocomplete="username" placeholder="ex. : prenom.nom"></div>
        <div><label>Courriel</label><input name="email" type="email"></div>
        <div><label>Mot de passe (8 caractères minimum)</label><input name="mot_de_passe" type="password" minlength="8" required autocomplete="new-password"></div>
        <div><label>Confirmer le mot de passe</label><input name="confirmation" type="password" required autocomplete="new-password"></div>
      </div>
      <div id="s-err" class="late" style="margin-top:.75rem"></div>
      <button class="btn primary" style="margin-top:1rem;width:100%">Créer le compte</button>
    </form>`;
  document.getElementById("setup").onsubmit = async (e) => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(e.target));
    const err = document.getElementById("s-err");
    if (data.mot_de_passe !== data.confirmation) return (err.textContent = "Les mots de passe ne correspondent pas.");
    try {
      const r = await api("/installation", { method: "POST", body: data });
      ME = r.utilisateur;
      renderUserMenu();
      location.hash = "#/";
      router();
    } catch (ex) {
      err.textContent = ex.message;
    }
  };
}

/* ------------------------------------------------------------------ */
/* Mon compte                                                          */
/* ------------------------------------------------------------------ */
async function viewAccount() {
  $app.innerHTML = `
    <h1>Mon compte</h1>
    <form id="acc" class="card" style="max-width:640px">
      <div class="form-grid">
        <div><label>Identifiant</label><input value="${esc(ME.identifiant)}" disabled></div>
        <div><label>Rôle</label><input value="${ME.role === "admin" ? "Administrateur" : "Gestionnaire"}" disabled></div>
        <div><label>Nom complet</label><input name="nom" value="${esc(ME.nom)}" required></div>
        <div><label>Courriel</label><input name="email" type="email" value="${esc(ME.email)}"></div>
      </div>
      <h2 style="margin-top:1.5rem">Changer de mot de passe</h2>
      <div class="form-grid">
        <div><label>Mot de passe actuel</label><input name="ancien_mot_de_passe" type="password" autocomplete="current-password"></div>
        <div><label>Nouveau mot de passe</label><input name="nouveau_mot_de_passe" type="password" minlength="8" autocomplete="new-password"></div>
      </div>
      <p class="hint">Laissez vide pour conserver le mot de passe actuel. Les autres appareils connectés seront déconnectés.</p>
      <button class="btn primary">Enregistrer</button>
    </form>`;
  document.getElementById("acc").onsubmit = async (e) => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(e.target));
    try {
      await api("/moi", { method: "PUT", body: data });
      Object.assign(ME, { nom: data.nom, email: data.email });
      renderUserMenu();
      toast("Compte mis à jour.");
      viewAccount();
    } catch (err) {
      toast(err.message, true);
    }
  };
}

/* ------------------------------------------------------------------ */
/* Administration : utilisateurs et sauvegarde                         */
/* ------------------------------------------------------------------ */
async function viewUsers(editId = null) {
  if (!isAdmin()) return ($app.innerHTML = '<div class="card">Accès réservé aux administrateurs.</div>');
  const [users, params] = await Promise.all([api("/utilisateurs"), api("/parametres")]);
  const cur = users.find((u) => u.id === editId) || { role: "gestionnaire", actif: 1 };
  const roleLabel = (r) => (r === "admin" ? "Administrateur" : "Gestionnaire");

  $app.innerHTML = `
    <h1>Administration</h1>
    <form id="uform" class="card">
      <h2>${editId ? `Modifier ${esc(cur.nom)}` : "Ajouter un utilisateur"}</h2>
      <div class="form-grid">
        <div><label>Nom complet *</label><input name="nom" value="${esc(cur.nom || "")}" required></div>
        <div><label>Identifiant *</label><input name="identifiant" value="${esc(cur.identifiant || "")}" ${editId ? "disabled" : "required"} placeholder="prenom.nom"></div>
        <div><label>Courriel</label><input name="email" type="email" value="${esc(cur.email || "")}"></div>
        <div><label>Rôle</label><select name="role">${["gestionnaire", "admin"].map((r) => `<option value="${r}" ${r === cur.role ? "selected" : ""}>${roleLabel(r)}</option>`).join("")}</select></div>
        <div><label>${editId ? "Nouveau mot de passe (laisser vide pour ne pas changer)" : "Mot de passe provisoire *"}</label>
          <input name="mot_de_passe" type="password" minlength="8" autocomplete="new-password" ${editId ? "" : "required"}></div>
        ${editId ? `<div><label>Compte</label><select name="actif"><option value="1" ${cur.actif ? "selected" : ""}>Actif</option><option value="" ${cur.actif ? "" : "selected"}>Désactivé</option></select></div>` : ""}
      </div>
      <p class="hint">Les administrateurs gèrent les comptes, la sauvegarde et peuvent supprimer des dossiers et des copropriétés.</p>
      <div class="actions"><button class="btn primary">${editId ? "Enregistrer" : "Créer le compte"}</button>
        ${editId ? '<button type="button" class="btn" id="u-cancel">Annuler</button>' : ""}</div>
    </form>
    <div class="card table-wrap" style="padding:0"><table>
      <thead><tr><th>Nom</th><th>Identifiant</th><th>Courriel</th><th>Rôle</th><th>Dernière connexion</th><th></th></tr></thead>
      <tbody>${users
        .map(
          (u) => `<tr class="${u.actif ? "" : "muted"}"><td><strong>${esc(u.nom)}</strong>${u.actif ? "" : " (désactivé)"}</td>
          <td>${esc(u.identifiant)}</td><td>${esc(u.email)}</td><td>${roleLabel(u.role)}</td>
          <td>${u.derniere_connexion ? fmtDate(u.derniere_connexion) : "—"}</td>
          <td><button class="btn small" data-uedit="${u.id}">Modifier</button></td></tr>`
        )
        .join("")}</tbody>
    </table></div>
    <div class="card">
      <h2>Champs personnalisés des dossiers</h2>
      <p class="muted">Ajoutez les informations propres à votre cabinet. Elles apparaissent dans le formulaire
      (« Informations complémentaires »), la fiche et l'export Excel.</p>
      <div id="champs-editor"></div>
    </div>
    <form id="pform" class="card">
      <h2>Coordonnées du cabinet (en-tête des courriers)</h2>
      <div class="form-grid">
        <div><label>Nom du cabinet</label><input name="cabinet_nom" value="${esc(params.cabinet_nom)}"></div>
        <div><label>Ville (« Fait à … »)</label><input name="cabinet_ville" value="${esc(params.cabinet_ville)}"></div>
        <div><label>Téléphone</label><input name="cabinet_telephone" value="${esc(params.cabinet_telephone)}"></div>
        <div><label>Courriel</label><input name="cabinet_email" value="${esc(params.cabinet_email)}"></div>
        <div class="full"><label>Adresse (plusieurs lignes possibles)</label><textarea name="cabinet_adresse" style="min-height:60px">${esc(params.cabinet_adresse)}</textarea></div>
        <div class="full"><label>Mentions de bas de page (carte professionnelle, garantie financière, SIRET…)</label><textarea name="cabinet_mentions" style="min-height:60px">${esc(params.cabinet_mentions)}</textarea></div>
      </div>
      <button class="btn primary" style="margin-top:.75rem">Enregistrer les coordonnées</button>
    </form>
    <div class="card">
      <h2>Sauvegarde</h2>
      <p class="muted">Télécharge une archive ZIP contenant la base de données et tous les documents joints. À conserver en lieu sûr (données personnelles).</p>
      <a class="btn" href="/api/sauvegarde.zip">Télécharger une sauvegarde complète</a>
    </div>`;

  document.getElementById("uform").onsubmit = async (e) => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(e.target));
    if (editId) data.actif = !!data.actif;
    try {
      if (editId) await api(`/utilisateurs/${editId}`, { method: "PUT", body: data });
      else await api("/utilisateurs", { method: "POST", body: data });
      toast(editId ? "Utilisateur modifié." : "Compte créé. Communiquez l'identifiant et le mot de passe provisoire à votre collègue.");
      viewUsers();
    } catch (err) {
      toast(err.message, true);
    }
  };
  document.getElementById("u-cancel")?.addEventListener("click", () => viewUsers());
  champsEditor(document.getElementById("champs-editor"), await api("/champs"));
  document.getElementById("pform").onsubmit = async (e) => {
    e.preventDefault();
    try {
      await api("/parametres", { method: "PUT", body: Object.fromEntries(new FormData(e.target)) });
      toast("Coordonnées du cabinet enregistrées.");
    } catch (err) {
      toast(err.message, true);
    }
  };
  $app.querySelectorAll("[data-uedit]").forEach((b) => b.addEventListener("click", () => viewUsers(Number(b.dataset.uedit))));
}

function champsEditor(el, champs) {
  const types = META.champ_types;
  const save = async (list) => {
    try {
      champs = await api("/champs", { method: "PUT", body: { champs: list } });
      toast("Champs enregistrés.");
      render();
    } catch (e) {
      toast(e.message, true);
    }
  };
  const render = () => {
    el.innerHTML = `
      ${champs.length ? `<div class="table-wrap"><table><thead><tr><th>Libellé</th><th>Type</th><th>Choix</th><th></th></tr></thead><tbody>
        ${champs.map((c, i) => `<tr>
          <td><input data-lib="${i}" value="${esc(c.libelle)}"></td>
          <td>${esc(types[c.type])}</td>
          <td>${c.type === "liste" ? `<input data-opt="${i}" value="${esc(c.options.join(", "))}">` : "—"}</td>
          <td class="actions">
            <button class="btn small" data-up="${i}" ${i ? "" : "disabled"} title="Monter">↑</button>
            <button class="btn small" data-down="${i}" ${i < champs.length - 1 ? "" : "disabled"} title="Descendre">↓</button>
            <button class="btn small danger" data-rm="${i}" title="Supprimer">✕</button>
          </td></tr>`).join("")}
      </tbody></table></div>
      <button class="btn" id="ch-save" style="margin:.5rem 0 1rem">Enregistrer les libellés modifiés</button>` : '<p class="muted">Aucun champ personnalisé.</p>'}
      <form id="ch-add" class="form-grid" style="align-items:end">
        <div><label>Nouveau champ : libellé</label><input name="libelle" required placeholder="ex. : Référence interne"></div>
        <div><label>Type</label><select name="type">${Object.entries(types).map(([k, l]) => `<option value="${k}">${esc(l)}</option>`).join("")}</select></div>
        <div><label>Choix (séparés par des virgules)</label><input name="options" placeholder="pour une liste de choix"></div>
        <div><button class="btn primary">Ajouter le champ</button></div>
      </form>`;
    const current = () => champs.map((c, i) => ({
      ...c,
      libelle: el.querySelector(`[data-lib="${i}"]`).value,
      options: c.type === "liste" ? el.querySelector(`[data-opt="${i}"]`).value : c.options,
    }));
    el.querySelector("#ch-add").onsubmit = (e) => {
      e.preventDefault();
      save([...current(), Object.fromEntries(new FormData(e.target))]);
    };
    el.querySelector("#ch-save")?.addEventListener("click", () => save(current()));
    el.querySelectorAll("[data-rm]").forEach((b) => b.addEventListener("click", () => {
      const i = Number(b.dataset.rm);
      if (!confirm(`Supprimer le champ « ${champs[i].libelle} » ? Les valeurs déjà saisies ne seront plus affichées.`)) return;
      save(current().filter((_, j) => j !== i));
    }));
    const move = (i, d) => { const l = current(); [l[i], l[i + d]] = [l[i + d], l[i]]; save(l); };
    el.querySelectorAll("[data-up]").forEach((b) => b.addEventListener("click", () => move(Number(b.dataset.up), -1)));
    el.querySelectorAll("[data-down]").forEach((b) => b.addEventListener("click", () => move(Number(b.dataset.down), 1)));
  };
  render();
}

boot();

/* ------------------------------------------------------------------ */
/* Tableau de bord                                                     */
/* ------------------------------------------------------------------ */
async function viewDashboard() {
  const scope = prefGet("dashboard-scope", "tous");
  const st = await api(scope === "moi" ? `/stats?gestionnaire=${ME.id}` : "/stats");
  const max = Math.max(1, ...Object.values(st.par_statut));
  const miniList = (items, empty, dateKey) =>
    items.length
      ? `<ul class="list-plain">${items
          .map(
            (s) => `<li><span><a href="#/sinistres/${s.id}">${esc(s.reference)}</a> — ${esc(s.copropriete_nom || "?")}
              <br><span class="muted">${esc(s.prochaine_action || s.type)}</span></span>
              <span class="${dateKey === "date_echeance" && isLate(s) ? "late" : "muted"}">${fmtDate(s[dateKey])}</span></li>`
          )
          .join("")}</ul>`
      : `<p class="muted">${empty}</p>`;

  $app.innerHTML = `
    <div class="page-head"><h1>Tableau de bord</h1>
      <select id="scope" style="width:auto"><option value="tous">Tous les dossiers</option>
        <option value="moi" ${scope === "moi" ? "selected" : ""}>Mes dossiers</option></select></div>
    <div class="grid kpis">
      <div class="card kpi"><div class="value">${st.ouverts}</div><div class="label">Dossiers ouverts</div></div>
      <div class="card kpi ${st.en_retard.length ? "alert" : ""}"><div class="value">${st.en_retard.length}</div><div class="label">Échéances dépassées</div></div>
      <div class="card kpi ${st.sans_suivi.length ? "alert" : ""}"><div class="value">${st.sans_suivi.length}</div><div class="label">Sans suivi depuis 30 j</div></div>
      <div class="card kpi"><div class="value">${fmtMoney(st.montant_estime_ouverts)}</div><div class="label">Estimé (dossiers ouverts)</div></div>
      <div class="card kpi"><div class="value">${fmtMoney(st.montant_indemnise_total)}</div><div class="label">Total indemnisé</div></div>
    </div>
    <div class="grid two">
      <div class="card"><h2>⚠ Échéances dépassées</h2>${miniList(st.en_retard, "Aucune échéance dépassée.", "date_echeance")}</div>
      <div class="card"><h2>Prochaines échéances</h2>${miniList(st.a_venir, "Aucune échéance planifiée.", "date_echeance")}</div>
      <div class="card"><h2>Dossiers à relancer (aucune action depuis 30 j)</h2>${miniList(st.sans_suivi, "Tous les dossiers ouverts sont suivis.", "derniere_action")}</div>
      <div class="card"><h2>Répartition par statut</h2>
        ${META.statuts
          .map(
            (s) => `<div class="bar-row"><span>${esc(s)}</span>
              <div><div class="bar" style="width:${(st.par_statut[s] / max) * 100}%"></div></div>
              <span class="num">${st.par_statut[s]}</span></div>`
          )
          .join("")}
      </div>
    </div>`;
  document.getElementById("scope").onchange = (e) => { prefSet("dashboard-scope", e.target.value); viewDashboard(); };
}

/* ------------------------------------------------------------------ */
/* Liste des sinistres                                                 */
/* ------------------------------------------------------------------ */
const listState = { q: "", copro: "", statut: "ouverts", type: "", gest: "", sort: "created_at", dir: -1 };

async function viewList() {
  const [sinistres, copros, users] = await Promise.all([api("/sinistres"), api("/coproprietes"), api("/utilisateurs")]);
  const opt = (v, l, sel) => `<option value="${esc(v)}" ${v === sel ? "selected" : ""}>${esc(l)}</option>`;

  $app.innerHTML = `
    <div class="page-head">
      <h1>Sinistres</h1>
      <div class="actions">
        <a class="btn" href="/api/export.csv">Exporter (Excel / CSV)</a>
        <a class="btn primary" href="#/sinistres/nouveau">+ Nouveau sinistre</a>
      </div>
    </div>
    <div class="filters">
      <input id="f-q" type="search" placeholder="Rechercher (réf., lot, déclarant, n° assureur…)" value="${esc(listState.q)}" style="flex:1;min-width:220px">
      <select id="f-copro">${opt("", "Toutes les copropriétés", listState.copro)}${copros.map((c) => opt(String(c.id), c.nom, listState.copro)).join("")}</select>
      <select id="f-statut">${opt("ouverts", "Dossiers ouverts", listState.statut)}${opt("", "Tous les statuts", listState.statut)}${META.statuts.map((s) => opt(s, s, listState.statut)).join("")}</select>
      <select id="f-type">${opt("", "Tous les types", listState.type)}${META.types.map((t) => opt(t, t, listState.type)).join("")}</select>
      <select id="f-gest">${gestionnaireOptions(users, listState.gest)}</select>
    </div>
    <div class="card table-wrap" style="padding:0"><table>
      <thead><tr>
        <th data-sort="reference">Réf.</th><th data-sort="copropriete_nom">Copropriété</th>
        <th data-sort="type">Type</th><th data-sort="localisation">Localisation</th><th data-sort="gestionnaire_nom">Gestionnaire</th>
        <th data-sort="date_survenance">Survenance</th><th data-sort="statut">Statut</th>
        <th data-sort="date_echeance">Échéance</th><th class="num" data-sort="montant_estime">Estimé</th>
      </tr></thead>
      <tbody id="rows"></tbody>
    </table></div>
    <p class="muted" id="count"></p>`;

  const render = () => {
    const q = listState.q.toLowerCase();
    let rows = sinistres.filter((s) => {
      if (listState.copro && String(s.copropriete_id) !== listState.copro) return false;
      if (listState.statut === "ouverts" && isClos(s)) return false;
      if (listState.statut && listState.statut !== "ouverts" && s.statut !== listState.statut) return false;
      if (listState.type && s.type !== listState.type) return false;
      if (listState.gest === "moi" && s.gestionnaire_id !== ME.id) return false;
      if (listState.gest && listState.gest !== "moi" && String(s.gestionnaire_id) !== listState.gest) return false;
      if (q) {
        const hay = [s.reference, s.copropriete_nom, s.localisation, s.numero_lot, s.declarant, s.tiers, s.numero_sinistre_assureur, s.description, s.expert, s.entreprise, ...Object.values(s.champs_perso || {})]
          .join(" ").toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
    const k = listState.sort;
    rows.sort((a, b) => {
      const va = a[k] ?? "", vb = b[k] ?? "";
      return (typeof va === "number" && typeof vb === "number" ? va - vb : String(va).localeCompare(String(vb), "fr")) * listState.dir;
    });
    document.getElementById("rows").innerHTML = rows.length
      ? rows
          .map(
            (s) => `<tr data-id="${s.id}">
        <td><strong>${esc(s.reference)}</strong>${s.nb_documents ? ` <span class="muted" title="${s.nb_documents} document(s)">📎${s.nb_documents}</span>` : ""}</td><td>${esc(s.copropriete_nom || "—")}</td>
        <td>${esc(s.type)}</td><td>${esc(s.localisation)}${s.numero_lot ? ` <span class="muted">(lot ${esc(s.numero_lot)})</span>` : ""}</td><td>${esc(s.gestionnaire_nom || "—")}</td><td>${fmtDate(s.date_survenance)}</td>
        <td>${badge(s.statut)}</td><td class="${isLate(s) ? "late" : ""}">${fmtDate(s.date_echeance)}</td>
        <td class="num">${fmtMoney(s.montant_estime)}</td></tr>`
          )
          .join("")
      : `<tr><td colspan="9" class="muted">Aucun sinistre ne correspond aux filtres.</td></tr>`;
    document.getElementById("count").textContent = `${rows.length} dossier(s) affiché(s) sur ${sinistres.length}.`;
  };

  document.getElementById("f-q").addEventListener("input", (e) => { listState.q = e.target.value; render(); });
  document.getElementById("f-copro").addEventListener("change", (e) => { listState.copro = e.target.value; render(); });
  document.getElementById("f-statut").addEventListener("change", (e) => { listState.statut = e.target.value; render(); });
  document.getElementById("f-type").addEventListener("change", (e) => { listState.type = e.target.value; render(); });
  document.getElementById("f-gest").addEventListener("change", (e) => { listState.gest = e.target.value; render(); });
  document.querySelectorAll("th[data-sort]").forEach((th) =>
    th.addEventListener("click", () => {
      listState.dir = listState.sort === th.dataset.sort ? -listState.dir : 1;
      listState.sort = th.dataset.sort;
      render();
    })
  );
  document.getElementById("rows").addEventListener("click", (e) => {
    const tr = e.target.closest("tr[data-id]");
    if (tr) location.hash = `#/sinistres/${tr.dataset.id}`;
  });
  render();
}

/* ------------------------------------------------------------------ */
/* Fiche sinistre                                                      */
/* ------------------------------------------------------------------ */
function delaiDeclaration(s) {
  const ref = s.date_constat || s.date_survenance;
  if (!ref || s.type === "Catastrophe naturelle") return null;
  const regle = DELAIS[s.type] || DELAI_DEFAUT;
  const fin = s.date_declaration || today();
  const n = regle.ouvres ? joursOuvres(ref, fin) : joursCalendaires(ref, fin);
  if (n <= regle.jours) return null;
  return s.date_declaration
    ? `Déclaré ${n} jour(s) ${regle.ouvres ? "ouvrés " : ""}après le constat (délai : ${regle.libelle}).`
    : `Non déclaré depuis ${n} jour(s) ${regle.ouvres ? "ouvrés" : ""} (délai : ${regle.libelle}).`;
}

async function viewDetail(id) {
  const [s, champs] = await Promise.all([api(`/sinistres/${id}`), api("/champs")]);
  const perso = champs.map((d) => [d, champDisplay(d, s.champs_perso[d.cle])]).filter(([, v]) => v);
  const row = (label, val) => (val ? `<dt>${label}</dt><dd>${val}</dd>` : "");
  const alerte = !isClos(s) ? delaiDeclaration(s) : null;
  const solde = s.montant_indemnise != null && s.montant_devis != null ? s.montant_devis - s.montant_indemnise : null;

  $app.innerHTML = `
    <div class="page-head">
      <div>
        <h1>${esc(s.reference)} — ${esc(s.type)}</h1>
        <div class="actions">${badge(s.statut)} <span class="muted">${esc(s.copropriete_nom || "Sans copropriété")}${s.localisation ? " · " + esc(s.localisation) : ""}${s.numero_lot ? ` · lot n° ${esc(s.numero_lot)}` : ""} · suivi par ${esc(s.gestionnaire_nom || "personne")}</span></div>
      </div>
      <div class="actions">
        <a class="btn" href="#/sinistres">← Liste</a>
        <button class="btn" id="print">Imprimer</button>
        <a class="btn" href="#/sinistres/${s.id}/courrier">✉ Courrier</a>
        <a class="btn primary" href="#/sinistres/${s.id}/modifier">Modifier</a>
        ${isAdmin() ? '<button class="btn danger" id="del">Supprimer</button>' : ""}
      </div>
    </div>
    ${alerte ? `<div class="card" style="border-color:var(--warn);background:var(--warn-soft)"><strong class="hint warn" style="font-size:.95rem">⚠ ${esc(alerte)}</strong></div>` : ""}
    ${s.prochaine_action || s.date_echeance ? `<div class="card"><strong>Prochaine action :</strong> ${esc(s.prochaine_action || "—")}
      ${s.date_echeance ? ` — <span class="${isLate(s) ? "late" : ""}">échéance le ${fmtDate(s.date_echeance)}</span>` : ""}</div>` : ""}
    <div class="grid two">
      <div class="card"><h2>Le sinistre</h2><dl class="details">
        ${row("Survenance", fmtDate(s.date_survenance))}
        ${row("Constat", fmtDate(s.date_constat))}
        ${row("Parties", esc(s.parties))}
        ${row("Localisation", esc(s.localisation))}
        ${row("Déclarant", esc(s.declarant))}
        ${row("Contact", esc(s.declarant_contact))}
        ${row("Tiers / responsable", esc(s.tiers))}
        ${row("Description", esc(s.description))}
        ${perso.map(([d, v]) => row(esc(d.libelle), v)).join("")}
      </dl></div>
      <div class="card"><h2>Assurance & expertise</h2><dl class="details">
        ${row("Assureur", esc(s.assureur))}
        ${row("N° sinistre assureur", esc(s.numero_sinistre_assureur))}
        ${row("Déclaration", fmtDate(s.date_declaration))}
        ${row("Convention", esc(s.convention))}
        ${row("Expert", esc(s.expert))}
        ${row("Date d'expertise", fmtDate(s.date_expertise))}
        ${row("Entreprise", esc(s.entreprise))}
      </dl></div>
      <div class="card"><h2>Montants</h2><dl class="details">
        ${row("Estimation", fmtMoney(s.montant_estime))}
        ${row("Devis travaux", fmtMoney(s.montant_devis))}
        ${row("Franchise", fmtMoney(s.franchise))}
        ${row("Indemnisé", fmtMoney(s.montant_indemnise))}
        ${row("Date de règlement", fmtDate(s.date_indemnisation))}
        ${solde !== null ? row("Reste à charge (devis − indemnité)", fmtMoney(solde)) : ""}
        ${row("Clôturé le", fmtDate(s.date_cloture))}
      </dl></div>
      <div class="card" id="docs-card"><h2>Documents (${s.documents.length})</h2>
        <div class="dropzone no-print" id="dropzone">
          <p>Glissez-déposez des fichiers ici (photos, constat, rapport, devis…)</p>
          <div class="actions" style="justify-content:center">
            <select id="doc-cat">${META.doc_categories.map((c) => `<option>${esc(c)}</option>`).join("")}</select>
            <label class="btn" style="margin:0">Choisir des fichiers<input type="file" id="doc-input" multiple hidden></label>
          </div>
          <div id="upload-status" class="hint"></div>
        </div>
        ${renderDocs(s.documents)}
      </div>
      <div class="card" id="journal-card"><h2>Journal de suivi</h2>
        <form id="jform" class="no-print" style="margin-bottom:1rem">
          <div class="form-grid">
            <div><label>Date</label><input type="date" name="date" value="${today()}"></div>
            <div><label>Type</label><select name="type">${META.journal_types.filter((t) => t !== "Changement de statut").map((t) => `<option>${esc(t)}</option>`).join("")}</select></div>
            <div class="full"><label>Commentaire</label><textarea name="commentaire" required placeholder="Ex. : relance de l'assureur par courriel, attente du rapport d'expertise…"></textarea></div>
          </div>
          <button class="btn primary" style="margin-top:.5rem">Ajouter au journal</button>
        </form>
        <ul class="timeline">${
          s.journal
            .map(
              (j) => `<li><div class="meta"><strong>${fmtDate(j.date)}</strong> · ${esc(j.type)}${j.auteur ? ` · ${esc(j.auteur)}` : ""}
            <button class="btn small no-print" data-jdel="${j.id}" title="Supprimer">✕</button></div>
            <div class="txt">${esc(j.commentaire)}</div></li>`
            )
            .join("") || '<li class="muted">Aucune entrée.</li>'
        }</ul>
      </div>
    </div>`;

  document.getElementById("print").onclick = () => window.print();
  bindDocs(s, () => viewDetail(id));
  if (isAdmin()) document.getElementById("del").onclick = async () => {
    if (!confirm(`Supprimer définitivement le dossier ${s.reference} et son journal ?`)) return;
    await api(`/sinistres/${s.id}`, { method: "DELETE" });
    toast("Dossier supprimé.");
    location.hash = "#/sinistres";
  };
  document.getElementById("jform").onsubmit = async (e) => {
    e.preventDefault();
    const fd = Object.fromEntries(new FormData(e.target));
    try {
      await api(`/sinistres/${s.id}/journal`, { method: "POST", body: fd });
      toast("Entrée ajoutée.");
      viewDetail(id);
    } catch (err) {
      toast(err.message, true);
    }
  };
  document.querySelectorAll("[data-jdel]").forEach((b) =>
    b.addEventListener("click", async () => {
      if (!confirm("Supprimer cette entrée du journal ?")) return;
      await api(`/journal/${b.dataset.jdel}`, { method: "DELETE" });
      viewDetail(id);
    })
  );
}

/* ------------------------------------------------------------------ */
/* Documents                                                           */
/* ------------------------------------------------------------------ */
function fmtSize(n) {
  if (n < 1024) return `${n} o`;
  if (n < 1048576) return `${(n / 1024).toFixed(0)} Ko`;
  return `${(n / 1048576).toFixed(1).replace(".", ",")} Mo`;
}
const isImage = (d) => /^image\/(png|jpeg|gif|webp)$/.test(d.mime);
function docIcon(d) {
  if (d.mime === "application/pdf") return "PDF";
  const ext = (d.nom.split(".").pop() || "").toUpperCase();
  return ext.length <= 5 ? ext : "FICHIER";
}

function renderDocs(docs) {
  if (!docs.length) return '<p class="muted">Aucun document.</p>';
  const groups = {};
  docs.forEach((d) => (groups[d.categorie] = groups[d.categorie] || []).push(d));
  return Object.entries(groups)
    .map(([cat, list]) => `<h3 class="doc-cat">${esc(cat)} <span class="muted">(${list.length})</span></h3>
      <div class="docs">${list
        .map(
          (d) => `<div class="doc">
          <a class="doc-thumb" href="/api/documents/${d.id}" target="_blank" rel="noopener" title="Ouvrir">
            ${isImage(d) ? `<img src="/api/documents/${d.id}" alt="" loading="lazy">` : `<span>${esc(docIcon(d))}</span>`}
          </a>
          <div class="doc-name" title="${esc(d.nom)}">${esc(d.nom)}</div>
          <div class="doc-meta">${fmtDate(d.created_at)} · ${fmtSize(d.taille)}${d.auteur ? `<br>${esc(d.auteur)}` : ""}</div>
          <div class="doc-actions no-print">
            <a class="btn small" href="/api/documents/${d.id}?download=1" title="Télécharger">⬇</a>
            <select class="doc-recat" data-id="${d.id}" title="Changer de catégorie">${META.doc_categories
              .map((c) => `<option ${c === d.categorie ? "selected" : ""}>${esc(c)}</option>`)
              .join("")}</select>
            <button class="btn small danger" data-docdel="${d.id}" title="Supprimer">✕</button>
          </div>
        </div>`
        )
        .join("")}</div>`)
    .join("");
}

async function uploadFiles(sinistreId, files, categorie, statusEl) {
  let ok = 0;
  const errors = [];
  for (const [i, f] of [...files].entries()) {
    statusEl.textContent = `Envoi ${i + 1}/${files.length} : ${f.name}…`;
    if (f.size > META.max_upload) {
      errors.push(`${f.name} : trop volumineux (max ${fmtSize(META.max_upload)})`);
      continue;
    }
    try {
      const res = await fetch(`/api/sinistres/${sinistreId}/documents`, {
        method: "POST",
        body: f,
        headers: {
          "Content-Type": f.type || "application/octet-stream",
          "X-Filename": encodeURIComponent(f.name),
          "X-Categorie": encodeURIComponent(categorie),
          "X-Requested-With": "fetch",
        },
      });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).erreur || `Erreur ${res.status}`);
      ok++;
    } catch (e) {
      errors.push(`${f.name} : ${e.message}`);
    }
  }
  statusEl.textContent = "";
  if (ok) toast(`${ok} document(s) ajouté(s).`);
  if (errors.length) toast(errors.join(" — "), true);
  return ok;
}

function bindDocs(s, reload) {
  const zone = document.getElementById("dropzone");
  const input = document.getElementById("doc-input");
  const status = document.getElementById("upload-status");
  const cat = document.getElementById("doc-cat");
  const send = async (files) => {
    if (!files.length) return;
    if (await uploadFiles(s.id, files, cat.value, status)) reload();
  };
  input.addEventListener("change", () => send(input.files));
  ["dragenter", "dragover"].forEach((ev) =>
    zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.add("over"); }));
  ["dragleave", "drop"].forEach((ev) =>
    zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.remove("over"); }));
  zone.addEventListener("drop", (e) => send(e.dataTransfer.files));

  document.querySelectorAll("[data-docdel]").forEach((b) =>
    b.addEventListener("click", async () => {
      const d = s.documents.find((x) => x.id === Number(b.dataset.docdel));
      if (!confirm(`Supprimer le document « ${d.nom} » ?`)) return;
      await api(`/documents/${d.id}`, { method: "DELETE" });
      toast("Document supprimé.");
      reload();
    })
  );
  document.querySelectorAll(".doc-recat").forEach((sel) =>
    sel.addEventListener("change", async () => {
      await api(`/documents/${sel.dataset.id}`, { method: "PUT", body: { categorie: sel.value } });
      toast("Catégorie modifiée.");
      reload();
    })
  );
}

/* ------------------------------------------------------------------ */
/* Formulaire sinistre                                                 */
/* ------------------------------------------------------------------ */
async function viewForm(id) {
  const [copros, users, champs, s] = await Promise.all([
    api("/coproprietes"),
    api("/utilisateurs"),
    api("/champs"),
    id ? api(`/sinistres/${id}`) : Promise.resolve({ statut: "À déclarer", type: "Dégât des eaux", parties: "Communes", date_constat: today(), gestionnaire_id: ME.id, champs_perso: {} }),
  ]);
  const v = (k) => esc(s[k] ?? "");
  const opts = (list, sel) => list.map((x) => `<option value="${esc(x)}" ${x === sel ? "selected" : ""}>${esc(x || "—")}</option>`).join("");
  const input = (name, label, type = "text", extra = "") =>
    `<div ${extra}><label for="${name}">${label}</label><input id="${name}" name="${name}" type="${type}" value="${v(name)}" ${type === "number" ? 'step="0.01" min="0"' : ""}></div>`;

  $app.innerHTML = `
    <div class="page-head"><h1>${id ? `Modifier ${esc(s.reference)}` : "Nouveau sinistre"}</h1></div>
    ${copros.length ? "" : `<div class="card" style="border-color:var(--warn)">Aucune copropriété enregistrée. <a href="#/coproprietes">Ajoutez-en une</a> pour rattacher vos dossiers.</div>`}
    <form id="sform" class="card">
      <fieldset><legend>Identification</legend><div class="form-grid">
        <div><label for="copropriete_id">Copropriété</label><select id="copropriete_id" name="copropriete_id">
          <option value="">—</option>${copros.map((c) => `<option value="${c.id}" ${c.id === s.copropriete_id ? "selected" : ""}>${esc(c.nom)}</option>`).join("")}</select></div>
        <div><label for="type">Type de sinistre *</label><select id="type" name="type">${opts(META.types, s.type)}</select></div>
        <div><label for="statut">Statut *</label><select id="statut" name="statut">${opts(META.statuts, s.statut)}</select></div>
        <div><label for="parties">Parties concernées</label><select id="parties" name="parties">${opts(["Communes", "Privatives", "Communes et privatives"], s.parties)}</select></div>
        ${input("localisation", "Localisation (bâtiment, étage, local…)")}
        ${input("numero_lot", "N° de lot")}
        <div><label for="gestionnaire_id">Gestionnaire du dossier</label><select id="gestionnaire_id" name="gestionnaire_id">
          <option value="">—</option>${gestionnaireOptions(users, s.gestionnaire_id, { all: false })}</select></div>
      </div></fieldset>
      ${champs.length ? `<fieldset><legend>Informations complémentaires</legend><div class="form-grid">
        ${champs.map((d) => champInput(d, s.champs_perso[d.cle])).join("")}</div></fieldset>` : ""}

      <fieldset><legend>Dates</legend><div class="form-grid">
        ${input("date_survenance", "Date de survenance", "date")}
        ${input("date_constat", "Date de constat / connaissance", "date")}
        ${input("date_declaration", "Date de déclaration à l'assureur", "date")}
      </div><div id="delai-hint" class="hint warn"></div></fieldset>

      <fieldset><legend>Personnes</legend><div class="form-grid">
        ${input("declarant", "Déclarant (copropriétaire, locataire, gardien…)")}
        ${input("declarant_contact", "Téléphone / courriel du déclarant")}
        ${input("tiers", "Tiers / responsable présumé", "text", 'class="full"')}
        <div class="full"><label for="description">Description / circonstances</label><textarea id="description" name="description">${v("description")}</textarea></div>
      </div></fieldset>

      <fieldset><legend>Assurance & expertise</legend><div class="form-grid">
        ${input("assureur", "Assureur")}
        ${input("numero_sinistre_assureur", "N° de sinistre assureur")}
        <div><label for="convention">Convention</label><select id="convention" name="convention">${opts(CONVENTIONS.includes(s.convention || "") ? CONVENTIONS : [...CONVENTIONS, s.convention], s.convention || "")}</select>
          <div id="conv-hint" class="hint"></div></div>
        ${input("expert", "Expert / cabinet")}
        ${input("date_expertise", "Date d'expertise", "date")}
        ${input("entreprise", "Entreprise de réparation")}
      </div></fieldset>

      <fieldset><legend>Montants (€)</legend><div class="form-grid">
        ${input("montant_estime", "Estimation des dommages (HT)", "number")}
        ${input("montant_devis", "Montant du devis", "number")}
        ${input("franchise", "Franchise", "number")}
        ${input("montant_indemnise", "Montant indemnisé", "number")}
        ${input("date_indemnisation", "Date de règlement", "date")}
      </div></fieldset>

      <fieldset><legend>Suivi</legend><div class="form-grid">
        ${input("prochaine_action", "Prochaine action à mener", "text", 'class="full"')}
        ${input("date_echeance", "Échéance / date de relance", "date")}
        ${input("date_cloture", "Date de clôture", "date")}
      </div></fieldset>

      <div class="actions">
        <button class="btn primary" type="submit">${id ? "Enregistrer" : "Créer le dossier"}</button>
        <a class="btn" href="${id ? `#/sinistres/${id}` : "#/sinistres"}">Annuler</a>
      </div>
    </form>`;

  const form = document.getElementById("sform");
  const get = (n) => form.elements[n].value;

  // Pré-remplit l'assureur depuis la fiche copropriété
  form.elements.copropriete_id.addEventListener("change", () => {
    const c = copros.find((c) => String(c.id) === get("copropriete_id"));
    if (c && !get("assureur")) form.elements.assureur.value = c.assureur || "";
  });

  const refreshHints = () => {
    const msg = delaiDeclaration({ type: get("type"), date_survenance: get("date_survenance"), date_constat: get("date_constat"), date_declaration: get("date_declaration") });
    const regle = get("type") === "Catastrophe naturelle" ? DELAIS["Catastrophe naturelle"] : DELAIS[get("type")] || DELAI_DEFAUT;
    document.getElementById("delai-hint").textContent = msg ? "⚠ " + msg : "";
    document.getElementById("delai-hint").className = msg ? "hint warn" : "hint";
    if (!msg) document.getElementById("delai-hint").textContent = `Délai de déclaration : ${regle.libelle}.`;

    const ch = document.getElementById("conv-hint");
    const m = parseFloat(get("montant_estime"));
    if (get("type") === "Dégât des eaux" && !isNaN(m)) {
      const sugg = m <= 1600 ? CONVENTIONS[1] : m <= 5000 ? CONVENTIONS[2] : CONVENTIONS[3];
      ch.innerHTML = `Suggestion : <a href="#" id="conv-apply">${esc(sugg)}</a>`;
      document.getElementById("conv-apply").onclick = (e) => { e.preventDefault(); form.elements.convention.value = sugg; };
    } else ch.textContent = "";
  };
  form.addEventListener("input", refreshHints);
  form.addEventListener("change", refreshHints);
  refreshHints();

  form.onsubmit = async (e) => {
    e.preventDefault();
    const data = extractChamps(Object.fromEntries(new FormData(form)), champs);
    try {
      const saved = id
        ? await api(`/sinistres/${id}`, { method: "PUT", body: data })
        : await api("/sinistres", { method: "POST", body: data });
      toast(id ? "Dossier enregistré." : `Dossier ${saved.reference} créé.`);
      location.hash = `#/sinistres/${saved.id}`;
    } catch (err) {
      toast(err.message, true);
    }
  };
}

/* ------------------------------------------------------------------ */
/* Copropriétés                                                        */
/* ------------------------------------------------------------------ */
async function viewCopros(editId = null) {
  const copros = await api("/coproprietes");
  const cur = copros.find((c) => c.id === editId) || {};
  const f = (name, label, full = false) =>
    `<div ${full ? 'class="full"' : ""}><label for="c-${name}">${label}</label><input id="c-${name}" name="${name}" value="${esc(cur[name] ?? "")}" ${name === "nom" ? "required" : ""}></div>`;

  $app.innerHTML = `
    <h1>Copropriétés</h1>
    <form id="cform" class="card">
      <h2>${editId ? "Modifier la copropriété" : "Ajouter une copropriété"}</h2>
      <div class="form-grid">
        ${f("nom", "Nom *")}${f("adresse", "Adresse")}
        ${f("assureur", "Assureur multirisque immeuble")}${f("numero_police", "N° de police")}
        ${f("assureur_adresse", "Adresse postale de l'assureur (service sinistres)")}${f("assureur_email", "Courriel de l'assureur (sinistres)")}
        ${f("courtier", "Courtier")}${f("notes", "Notes")}
      </div>
      <div class="actions" style="margin-top:.9rem">
        <button class="btn primary">${editId ? "Enregistrer" : "Ajouter"}</button>
        ${editId ? '<button type="button" class="btn" id="c-cancel">Annuler</button>' : ""}
      </div>
    </form>
    <div class="card table-wrap" style="padding:0"><table>
      <thead><tr><th>Nom</th><th>Adresse</th><th>Assureur</th><th>N° police</th><th class="num">Ouverts</th><th class="num">Total</th><th></th></tr></thead>
      <tbody>${
        copros
          .map(
            (c) => `<tr><td><strong>${esc(c.nom)}</strong></td><td>${esc(c.adresse)}</td><td>${esc(c.assureur)}</td>
          <td>${esc(c.numero_police)}</td><td class="num">${c.nb_ouverts}</td><td class="num">${c.nb_sinistres}</td>
          <td class="actions"><button class="btn small" data-edit="${c.id}">Modifier</button>
          ${isAdmin() ? `<button class="btn small danger" data-del="${c.id}">Supprimer</button>` : ""}</td></tr>`
          )
          .join("") || '<tr><td colspan="7" class="muted">Aucune copropriété.</td></tr>'
      }</tbody>
    </table></div>`;

  document.getElementById("cform").onsubmit = async (e) => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(e.target));
    try {
      if (editId) await api(`/coproprietes/${editId}`, { method: "PUT", body: data });
      else await api("/coproprietes", { method: "POST", body: data });
      toast("Copropriété enregistrée.");
      viewCopros();
    } catch (err) {
      toast(err.message, true);
    }
  };
  document.getElementById("c-cancel")?.addEventListener("click", () => viewCopros());
  $app.querySelectorAll("[data-edit]").forEach((b) => b.addEventListener("click", (e) => { e.stopPropagation(); viewCopros(Number(b.dataset.edit)); }));
  $app.querySelectorAll("[data-del]").forEach((b) =>
    b.addEventListener("click", async (e) => {
      e.stopPropagation();
      const c = copros.find((x) => x.id === Number(b.dataset.del));
      const warn = c.nb_sinistres ? ` Ses ${c.nb_sinistres} sinistre(s) seront conservés mais détachés.` : "";
      if (!confirm(`Supprimer « ${c.nom} » ?${warn}`)) return;
      await api(`/coproprietes/${c.id}`, { method: "DELETE" });
      viewCopros();
    })
  );
}
