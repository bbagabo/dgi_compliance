// DGI Compliance v5.3.2 - lecture, test et statut d'un MCF physique depuis le bureau ERPNext.
//
// ERPNext (cloud) ne voit pas les ports USB : le navigateur du PC ou le MCF est branche le lit (Web Serial,
// Chrome / Edge, HTTPS). Commandes sans effet fiscal uniquement : C1h (etat), C2h (connexion DGI), 2Bh
// (contribuable, facultatif). Le port est ferme apres chaque lecture, pour ne pas bloquer la page Poste MCF.
// Les comparaisons (NID, MID, NIF, taux, poste) sont faites par le serveur : fiscal.mcf_status.record_desk_test.
// Requiert /assets/dgi_compliance/js/dgi_mcf.js (window.DGIMCF).
(function () {
	const esc = (s) => frappe.utils.escape_html(s === undefined || s === null ? "" : String(s));
	const STATES = { DIS: __("non connecte au reseau"), CON: __("connecte, aucun envoi en cours"),
		TRA: __("envoi de donnees en cours"), RES: __("redemarrage en cours") };
	const LEVEL = { ok: ["green", "&#10003;"], warn: ["orange", "!"], fail: ["red", "&#10007;"] };
	const RESULT_COLOR = { OK: "green", Avertissement: "orange", Ecart: "red", Echec: "red", "Non teste": "gray" };

	// AAAAMMJJHHmmss (horloge du MCF) -> Date locale du PC
	function mcf_date(v) {
		const m = /^(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})$/.exec(v || "");
		return m ? new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6]) : null;
	}
	function mcf_dt_user(v) {
		const m = /^(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})$/.exec(v || "");
		return m ? frappe.datetime.str_to_user(`${m[1]}-${m[2]}-${m[3]} ${m[4]}:${m[5]}:${m[6]}`) : (v || "");
	}
	function duration(s) {
		s = Math.abs(Math.round(s || 0));
		if (s < 120) return __("{0} s", [s]);
		if (s < 7200) return __("{0} min", [Math.round(s / 60)]);
		if (s < 3 * 86400) return __("{0} h", [Math.round(s / 3600)]);
		return __("{0} jours", [Math.round(s / 86400)]);
	}
	function when(v) {
		return v ? `${esc(frappe.datetime.str_to_user(v))} <span class="text-muted">(${esc(frappe.datetime.comment_when(v))})</span>` : esc(__("jamais"));
	}
	function pill(color, text) {
		return `<span class="indicator-pill ${color || "gray"}">${esc(text)}</span>`;
	}

	function browser_problem() {
		if (!window.isSecureContext) return __("Cette page doit etre ouverte en HTTPS pour lire le MCF.");
		if (!("serial" in navigator)) return __("Ce navigateur ne lit pas les ports serie : ouvrez ERPNext dans Google Chrome ou Microsoft Edge a jour, sur le PC ou le MCF est branche.");
		return null;
	}

	// C2h (protocole SFE-MCF v1.0) : EC envoyees, DC disponibles dans le MCF, DT derniere connexion reussie,
	// STA DIS/CON/TRA/RES, ER dernier message du fabricant. Alerte au-dela d'un jour (exigence du protocole).
	function c2h_html(srv, mcf_dt) {
		if (!srv) return "";
		if (srv.read_error) return `<span class="text-danger">${esc(srv.read_error)}</span>`;
		const last = mcf_date(srv.last_sync), now = mcf_date(mcf_dt) || new Date();
		const old = !last || now - last > 24 * 3600 * 1000;
		const sent = parseInt(srv.sent, 10), avail = parseInt(srv.pending, 10);
		const unsent = isNaN(sent) || isNaN(avail) ? null : Math.max(0, avail - sent);
		return `${esc(srv.state)} (${esc(STATES[srv.state] || __("etat inconnu"))})<br>`
			+ `${esc(__("Transactions envoyees"))} : ${esc(srv.sent)} · ${esc(__("disponibles dans le MCF"))} : ${esc(srv.pending)}`
			+ (unsent !== null ? ` · <b class="${unsent ? "text-warning" : ""}">${esc(__("non envoyees"))} : ${unsent}</b>` : "") + "<br>"
			+ `<span class="${old ? "text-danger" : ""}">${esc(__("Derniere connexion reussie a la DGI"))} : ${last ? esc(mcf_dt_user(srv.last_sync)) : esc(__("jamais"))}`
			+ `${old ? " - " + esc(__("plus d'un jour : verifiez la connexion du MCF")) : ""}</span>`
			+ (srv.error ? `<br><small>${esc(__("Dernier message du MCF"))} : ${esc(srv.error)}</small>` : "");
	}

	// ------------------------------------------------------------------ lecture (sans effet fiscal)
	async function read_port(port, { baudRate = 115200, taxpayer = false, trace } = {}) {
		const M = window.DGIMCF;
		const t = await new M.WebSerialTransport(port, { baudRate }).open();
		const dev = new M.MCFDevice(t, { trace });
		try {
			return await dev.lock(async () => {
				const t0 = performance.now();
				const st = await dev.status();
				const elapsed_ms = Math.round(performance.now() - t0);
				const pc_epoch = Date.now();
				let srv;
				try { srv = await dev.serverStatus(); } catch (e) { srv = { read_error: e.message }; }   // ER != echec de lecture
				const tp = {};
				if (taxpayer) {
					for (const opt of ["I0", "I1", "I2", "I3", "I4"]) {
						try { tp[opt] = await dev.taxpayer(opt); } catch (e) { tp[opt] = ""; }
					}
				}
				return { st, srv, tp, port: t.name, elapsed_ms, pc_epoch };
			});
		} finally {
			try { await dev.close(); } catch (e) { /* deja ferme */ }
		}
	}

	// Ports deja autorises pour ce site dans ce navigateur (aucune fenetre) : premier MCF qui repond,
	// celui du NID attendu s'il est donne.
	async function find_known(expected_nid, opts) {
		if (browser_problem()) return null;
		let busy = null;
		for (const port of await navigator.serial.getPorts()) {
			try {
				const read = await read_port(port, opts);
				if (!expected_nid || read.st.nid === expected_nid) return { port, read };
			} catch (e) {
				if (/open|ouvr|busy|Access|already/i.test(String((e && e.message) || e))) busy = e;
			}
		}
		if (busy) { const e = new Error(__("Le port du MCF est deja utilise (page Poste MCF ouverte dans un autre onglet, ou autre programme).")); e.busy = true; throw e; }
		return null;
	}

	async function choose_and_read(expected_nid, opts) {
		const found = await find_known(expected_nid, opts);
		if (found) return found;
		const port = await navigator.serial.requestPort({});      // NotFoundError si la fenetre est fermee
		return { port, read: await read_port(port, opts) };
	}

	function observation(read) {
		const { st, srv, tp, port, elapsed_ms, pc_epoch } = read;
		const mcf = mcf_date(st.dt);
		const obs = {
			mid: st.mid, nid: st.nid, nif: st.nif, dt: st.dt, tc: st.tc, fvc: st.fvc, frc: st.frc,
			group_count: st.group_count, tax_rates: st.tax_rates, flags: st.flags || [], invoice_open: !!st.invoice_open,
			last_invoice: st.dfn || "", dgi: srv, port, elapsed_ms,
			clock_offset_s: mcf && pc_epoch ? Math.round((mcf.getTime() - pc_epoch) / 1000) : null,
		};
		if (tp && tp.I0 !== undefined) {
			Object.assign(obs, { taxpayer_name: tp.I0 || "", taxpayer_address: [tp.I1, tp.I2].filter(Boolean).join(", "),
				taxpayer_phone: tp.I3 || "", taxpayer_email: tp.I4 || "" });
		}
		return obs;
	}

	function read_table(read) {
		const { st, srv, port } = read;
		const rates = Object.entries(st.tax_rates || {}).map(([g, r]) => `${g} ${r}%`).join(" · ");
		const rows = [
			[__("Port"), esc(port)], [__("MID (NID-activation)"), esc(st.mid)], [__("NIF du MCF"), esc(st.nif)],
			[__("Date du MCF"), esc(mcf_dt_user(st.dt))],
			[__("Compteurs (total / ventes / avoirs)"), esc(`${st.tc} / ${st.fvc} / ${st.frc}`)],
			[__("Groupes de taxation ({0})", [st.group_count]), esc(rates)],
			[__("Connexion DGI (C2h)"), c2h_html(srv, st.dt)],
		];
		return `<table class="table table-bordered table-sm small">${rows.map(([k, v]) =>
			`<tr><td style="width:34%">${esc(k)}</td><td>${v}</td></tr>`).join("")}</table>`;
	}

	function checks_html(checks) {
		if (!checks || !checks.length) return "";
		return `<table class="table table-bordered table-sm small" style="margin-bottom:8px">${checks.map((c) => {
			const [color, mark] = LEVEL[c.level] || LEVEL.warn;
			const rv = c.read || c.expected ? `<div class="text-muted">${c.read ? esc(__("lu")) + " : " + esc(c.read) : ""}${c.expected ? " · " + esc(__("attendu")) + " : " + esc(c.expected) : ""}</div>` : "";
			return `<tr><td style="width:30px;text-align:center"><span class="indicator-pill ${color}" style="padding:0 6px">${mark}</span></td>`
				+ `<td style="width:30%"><b>${esc(c.label)}</b></td><td>${esc(c.message)}${rv}</td></tr>`;
		}).join("")}</table>`;
	}

	// ------------------------------------------------------------------ test complet
	async function record(obs, { nid, origin, gateway_id }) {
		return frappe.xcall("dgi_compliance.fiscal.mcf_status.record_desk_test", {
			observation: JSON.stringify(obs), expected_nid: nid || null, origin: origin || "", gateway_id: gateway_id || null,
		});
	}

	// opts: { nid (attendu), origin, gateway_id, read (deja lu), on_done(result) }
	async function test(opts = {}) {
		const problem = browser_problem();
		if (problem && !opts.read) {
			frappe.msgprint({ title: __("Tester la connexion du MCF"), indicator: "orange", message: esc(problem) });
			return null;
		}
		let read = opts.read, failure = null;
		if (!read) {
			frappe.dom.freeze(__("Lecture du MCF (sans effet fiscal)..."));
			try {
				read = (await choose_and_read(opts.nid)).read;
			} catch (e) {
				if (e && e.name === "NotFoundError") { frappe.dom.unfreeze(); return null; }     // fenetre fermee
				failure = String((e && e.message) || e);
			} finally {
				frappe.dom.unfreeze();
			}
		}
		if (failure && !opts.nid) {
			frappe.msgprint({ title: __("Aucun MCF lu"), indicator: "red", message: esc(failure) });
			return null;
		}
		const obs = failure ? { error: failure } : observation(read);
		const r = await record(obs, opts);
		show_result(r, read, opts);
		if (opts.on_done) opts.on_done(r);
		return r;
	}

	function show_result(r, read, opts = {}) {
		const color = RESULT_COLOR[r.result] || "gray";
		const head = `<p>${pill(color, __(r.result))} <span class="text-muted">${esc(__("Test sans effet fiscal, {0}", [frappe.datetime.str_to_user(r.tested_on)]))}</span></p>`;
		const d = new frappe.ui.Dialog({
			title: __("Test de connexion du MCF {0}", [r.nid || ""]),
			size: "large",
			fields: [{ fieldtype: "HTML", fieldname: "body",
				options: head + checks_html(r.checks) + (read ? `<details><summary class="text-muted">${esc(__("Valeurs lues dans le MCF"))}</summary>${read_table(read)}</details>` : "") }],
			primary_action_label: r.known && !opts.stay ? __("Ouvrir la fiche MCF") : __("Fermer"),
			primary_action: () => { d.hide(); if (r.known && !opts.stay) frappe.set_route("Form", "DGI MCF Device", r.nid); },
		});
		d.show();
		return d;
	}

	// ------------------------------------------------------------------ statut (fiche, point de vente, page)
	function load_status(args) {
		return frappe.xcall("dgi_compliance.fiscal.mcf_status.mcf_status", args);
	}

	function status_html(s) {
		if (!s || !s.known) {
			return `<div class="text-muted">${esc(s && s.nid ? __("Le MCF {0} n'existe pas dans ERPNext.", [s.nid]) : __("Aucun MCF rattache."))}</div>`;
		}
		const t = s.test || {};
		const st_colors = { Detecte: "orange", Declare: "orange", Actif: "green", "A revalider": "red", Suspendu: "gray", Retire: "gray" };
		const lines = [];
		lines.push([__("Statut"), `${pill(s.indicator, s.label)} &nbsp; ${esc(__("Etat ERPNext"))} : ${pill(st_colors[s.status], __(s.status))}`
			+ (s.point_of_sale ? ` &nbsp; ${esc(__("Point de vente"))} : <a href="/app/dgi-point-of-sale/${encodeURIComponent(s.point_of_sale)}">${esc(s.point_of_sale)}</a>` : "")]);
		lines.push([__("Dernier test de connexion"), t.on
			? `${pill(RESULT_COLOR[t.result], __(t.result))} ${when(t.on)}${t.by ? " · " + esc(t.by) : ""}${t.origin ? " · " + esc(t.origin) : ""}`
			: esc(__("jamais : cliquez sur 'Tester la connexion (sur ce PC)' depuis le PC ou le MCF est branche"))]);
		const bad = (t.checks || []).filter((c) => c.level !== "ok");
		if (bad.length) lines.push([__("Points a verifier"), checks_html(bad)]);
		const dgi_state = t.dgi_state || (s.heartbeat || {}).dgi_state;
		lines.push([__("Connexion du MCF a la DGI"),
			(dgi_state ? `${esc(dgi_state)} (${esc(STATES[dgi_state] || "")}) · ` : "")
			+ `<span class="${s.dgi_sync_old ? "text-danger" : ""}">${esc(__("derniere connexion reussie"))} : ${when(s.dgi_last_sync)}`
			+ (s.dgi_sync_old ? " - " + esc(__("plus de {0} jour(s)", [s.warn_days])) : "") + "</span>"
			+ (t.unsent !== null && t.unsent !== undefined ? ` · ${esc(__("non envoyees"))} : ${esc(t.unsent)}` : "")]);
		const postes = (s.stations || []).map((g) => `<a href="/app/dgi-gateway/${encodeURIComponent(g.name)}">${esc(g.name)}</a> `
			+ pill(g.online ? "green" : (g.status === "Active" ? "red" : "orange"),
				g.online ? __("en ligne") : (g.status === "Active" ? __("hors ligne") : __(g.status)))
			+ (g.last_seen ? ` <span class="text-muted">${esc(__("dernier signal"))} ${esc(frappe.datetime.comment_when(g.last_seen))}</span>` : ""));
		lines.push([__("Poste MCF"), postes.length ? postes.join("<br>") : `<span class="text-warning">${esc(__("aucun poste : les factures ne peuvent pas etre transmises a ce MCF"))}</span>`
			+ ` · <a href="/app/dgi-mcf-station">${esc(__("page Poste MCF"))}</a>`]);
		if (t.clock_offset_s !== null && t.clock_offset_s !== undefined && Math.abs(t.clock_offset_s) > 120) {
			lines.push([__("Horloge du MCF"), `<span class="text-warning">${esc(__("ecart de {0} avec le PC au dernier test", [duration(t.clock_offset_s)]))}</span>`]);
		}
		return `<table class="table table-sm small" style="margin:0">${lines.map(([k, v]) =>
			`<tr><td style="width:24%" class="text-muted">${esc(k)}</td><td>${v}</td></tr>`).join("")}</table>`;
	}

	function require_libs(cb) {
		frappe.require(["/assets/dgi_compliance/js/dgi_mcf.js"], cb);
	}

	window.DGIMCFDesk = { mcf_date, mcf_dt_user, duration, browser_problem, c2h_html, read_port, find_known,
		choose_and_read, observation, read_table, checks_html, record, test, show_result, load_status, status_html,
		require_libs, RESULT_COLOR };
})();
