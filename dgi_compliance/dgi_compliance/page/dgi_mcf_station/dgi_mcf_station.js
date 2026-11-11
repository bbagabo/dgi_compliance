// Page "Poste MCF" (DGI Compliance v5.2.0): the cash desk where the MCF is plugged becomes the
// gateway of its MCF while this page is open in Chrome / Edge. See public/js/dgi_mcf.js.
// v5.3.2: the MCF plugged into this PC is read and shown even without a poste (ports already allowed for
// the site, no fiscal effect, port released after the reading), with its state in ERPNext, a connection test
// recorded on the MCF, and for a DGI Fiscal Manager the creation of the poste of its point de vente.
frappe.pages["dgi-mcf-station"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({ parent: wrapper, title: __("Poste MCF"), single_column: true });
	frappe.require(["/assets/dgi_compliance/js/dgi_mcf.js", "/assets/dgi_compliance/js/dgi_mcf_desk.js"], () => {
		wrapper.dgi_station = new DGIStationPage(page, wrapper);
	});
};

frappe.pages["dgi-mcf-station"].on_page_show = function (wrapper) {
	if (wrapper.dgi_station) wrapper.dgi_station.refresh_queue();
};

const STORE_KEY = "dgi-mcf-station";
const esc = (s) => frappe.utils.escape_html(s === undefined || s === null ? "" : String(s));
// v5.4.1: frappe.xcall rejects with `undefined` when ERPNext refuses with a message (shown in its own window):
// `e.message` then raised a TypeError and the page showed nothing.
const err_text = (e, fallback) => (e && e.message) || (typeof e === "string" && e)
	|| fallback || __("Refuse par ERPNext : lisez le message affiche.");

class DGIStationPage {
	constructor(page, wrapper) {
		this.page = page;
		this.wrapper = wrapper;
		this.M = window.DGIMCF;
		this.keys = new this.M.KeyStore();
		this.logs = [];
		this.frames = [];
		this.trace_on = false;
		this.station = null;
		this.device = null;
		this.gw = null;
		this.session = null;
		this.remembered = this.load_pref();
		this.render();
		this.check_browser() && this.init();
	}

	// ------------------------------------------------------------------ preferences (per browser)
	load_pref() {
		try { return JSON.parse(localStorage.getItem(STORE_KEY) || "{}"); } catch (e) { return {}; }
	}
	save_pref(patch) {
		this.remembered = Object.assign({}, this.remembered, patch);
		try { localStorage.setItem(STORE_KEY, JSON.stringify(this.remembered)); } catch (e) { /* private window */ }
	}

	// ------------------------------------------------------------------ layout
	render() {
		const $b = $(this.page.body).empty();
		$b.append(`
			<div class="dgi-st" style="max-width:1100px">
				<div class="dgi-st-alert"></div>
				<div class="row dgi-st-cards" style="margin-bottom:12px"></div>
				<div class="frappe-card" style="padding:16px;margin-bottom:12px">
					<div class="dgi-st-setup"></div>
				</div>
				<div class="frappe-card dgi-st-probe-card" style="padding:16px;margin-bottom:12px">
					<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
						<b>${__("MCF branche sur ce PC (lecture sans effet fiscal)")}</b>
						<span>
							<button class="btn btn-xs btn-default dgi-st-probe-read">${__("Lire le MCF branche")}</button>
							<button class="btn btn-xs btn-primary dgi-st-probe-test">${__("Tester la connexion")}</button>
						</span>
					</div>
					<div class="dgi-st-probe"></div>
				</div>
				<div class="frappe-card" style="padding:16px;margin-bottom:12px">
					<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
						<b>${__("Journal du poste")}</b>
						<span>
							<label style="font-weight:normal;margin-right:12px"><input type="checkbox" class="dgi-st-trace"> ${__("Trames MCF (hexadecimal)")}</label>
							<button class="btn btn-xs btn-default dgi-st-export">${__("Exporter le diagnostic")}</button>
						</span>
					</div>
					<pre class="dgi-st-log" style="max-height:320px;overflow:auto;font-size:11px;background:var(--subtle-fg);padding:8px;margin:0"></pre>
				</div>
			</div>`);
		$b.find(".dgi-st-trace").on("change", (e) => { this.trace_on = e.target.checked; });
		$b.find(".dgi-st-export").on("click", () => this.export_diagnostic());
		$b.find(".dgi-st-probe-read").on("click", () => this.probe_known(true));
		$b.find(".dgi-st-probe-test").on("click", () => this.test_connection());
		this.render_cards();
		this.render_probe();
	}
	card(title, value, color, detail) {
		return `<div class="col-sm-3"><div class="frappe-card" style="padding:12px;min-height:88px">
			<div class="text-muted small">${esc(title)}</div>
			<div style="font-size:16px;font-weight:600" class="indicator-pill ${color || "gray"}">${esc(value)}</div>
			<div class="text-muted small" style="margin-top:4px">${esc(detail || "")}</div></div></div>`;
	}
	render_cards() {
		const st = (this.station && this.station.state) || {};
		const dev = this.device_status || {};
		const pr = this.probe && this.probe.read;
		const mcf_card = this.device
			? this.card(__("MCF"), __("Connecte au poste"), "green", dev.mid ? `${dev.mid} - NIF ${dev.nif}` : "")
			: pr ? this.card(__("MCF"), __("Detecte sur ce PC"), "blue", `${pr.st.mid} - NIF ${pr.st.nif} - ${pr.port}`)
				: this.card(__("MCF"), __("Non connecte"), "red",
					(this.probe && this.probe.error) || __("Brancher le MCF puis cliquer sur Lire le MCF branche"));
		const cards = [
			mcf_card,
			this.card(__("Poste"), this.gw ? __(this.gw.status) : __("Non choisi"), this.gw && this.gw.status === "Active" ? "green" : "orange",
				this.gw ? `${this.gw.name} - ${this.gw.point_of_sale}` : ((this.stations && !this.stations.length) ? __("aucun poste cree") : "")),
			this.card(__("Service"), this.station && this.station.running ? __("En marche") : __("Arrete"),
				this.station && this.station.running ? (st.server === "connecte" ? "green" : "orange") : "gray",
				st.last_error ? st.last_error.slice(0, 80) : (st.server || "")),
			this.card(__("Factures"), `${st.done || 0} ${__("normalisee(s)")}`, "blue",
				this.queue_info !== undefined ? __("{0} en attente pour ce MCF", [this.queue_info]) : ""),
		];
		$(this.page.body).find(".dgi-st-cards").html(cards.join(""));
	}
	alert(html, color) {
		$(this.page.body).find(".dgi-st-alert").html(html ? `<div class="alert alert-${color || "warning"}">${html}</div>` : "");
	}
	log(msg) {
		const line = `${frappe.datetime.now_time()}  ${msg}`;
		this.logs.push(line);
		if (this.logs.length > 2000) this.logs.shift();
		const $l = $(this.page.body).find(".dgi-st-log");
		$l.text(this.logs.slice(-300).join("\n"));
		$l.scrollTop($l[0] ? $l[0].scrollHeight : 0);
	}
	trace(direction, bytes, label) {
		const line = `${new Date().toISOString()} ${direction} ${label} ${this.M.hex(bytes)}`;
		this.frames.push(line);
		if (this.frames.length > 3000) this.frames.shift();
		if (this.trace_on) this.log(`${direction} ${label} ${this.M.hex(bytes).slice(0, 120)}`);
	}

	// ------------------------------------------------------------------ checks
	check_browser() {
		if (!window.isSecureContext) {
			this.alert(__("Cette page doit etre ouverte en HTTPS (ou sur localhost) pour acceder au MCF et a la cle securisee."), "danger");
			return false;
		}
		if (!("serial" in navigator)) {
			this.alert(__("Ce navigateur ne permet pas d'acceder au port serie. Utilisez Google Chrome ou Microsoft Edge (version recente) sur l'ordinateur ou le MCF est branche."), "danger");
			return false;
		}
		if (!(window.crypto && crypto.subtle && window.indexedDB)) {
			this.alert(__("Fonctions de securite du navigateur indisponibles (WebCrypto / IndexedDB). Mode prive ?"), "danger");
			return false;
		}
		return true;
	}

	// ------------------------------------------------------------------ setup
	async init() {
		// one Poste MCF per browser: the serial port can only be driven by one tab
		const has_lock = await new Promise((resolve) => {
			if (!navigator.locks) return resolve(true);
			navigator.locks.request("dgi-mcf-station", { ifAvailable: true }, (l) => {
				if (!l) { resolve(false); return null; }
				resolve(true);
				return new Promise((release) => { this.release_lock = release; });   // held while the page lives
			}).catch(() => resolve(true));
		});
		if (!has_lock) {
			this.alert(__("Le Poste MCF est deja ouvert dans un autre onglet de ce navigateur : utilisez cet onglet."), "warning");
			return;
		}
		this.stations = await frappe.xcall("dgi_compliance.gateway.station.stations").catch((e) => {
			this.alert(esc(err_text(e)), "danger");
			return [];
		});
		this.render_setup();
		navigator.serial.addEventListener("connect", () => {
			if (this.station) this.station.wake();
			else if (!this.device) this.probe_known(false);          // v5.3.2: MCF plugged in again
		});
		navigator.serial.addEventListener("disconnect", () => {
			this.log(__("MCF debranche."));
			if (!this.device && this.probe) { this.probe = { error: __("MCF debranche") }; this.render_probe(); this.render_cards(); }
		});
		frappe.realtime.on("dgi_mcf_wake", (msg) => {
			if (this.station && this.gw && msg && msg.device === this.gw.mcf_device) {
				this.station.wake();
				this.refresh_queue();
			}
		});
		window.addEventListener("beforeunload", () => this.stop_session(true));
		if (this.remembered.gateway_id && this.stations.some((s) => s.name === this.remembered.gateway_id)) {
			await this.select(this.remembered.gateway_id, true);
		}
		if (!this.device) await this.probe_known(false);
	}

	render_setup() {
		const $s = $(this.page.body).find(".dgi-st-setup").empty();
		if (!this.stations.length) {
			$s.html(`<p>${__("Aucun poste MCF n'est prevu pour vos points de vente : le MCF peut etre lu et teste ci-dessous, mais il ne recoit pas de factures. Un DGI Fiscal Manager cree le poste (bouton 'Creer le poste MCF' ci-dessous, ou DGI Gateway de type 'Poste navigateur'), puis vous communique son code d'enrolement.")}</p>`);
			return;
		}
		const options = this.stations.map((s) => `<option value="${esc(s.name)}" ${this.gw && this.gw.name === s.name ? "selected" : ""}>${esc(s.name)} - ${esc(s.point_of_sale)} (${esc(__(s.status))})</option>`);
		$s.html(`
			<div class="row">
				<div class="col-sm-5"><label>${__("Poste MCF")}</label>
					<select class="form-control dgi-st-gw"><option value="">${__("Choisir...")}</option>${options.join("")}</select></div>
				<div class="col-sm-7" style="padding-top:22px">
					<button class="btn btn-primary btn-sm dgi-st-connect">${__("Connecter le MCF")}</button>
					<button class="btn btn-default btn-sm dgi-st-c1">${__("Lire l'etat (C1h)")}</button>
					<button class="btn btn-default btn-sm dgi-st-c2">${__("Connexion DGI (C2h)")}</button>
					<button class="btn btn-default btn-sm dgi-st-2b">${__("Contribuable (2Bh)")}</button>
					<button class="btn btn-default btn-sm dgi-st-propose">${__("Proposer ce MCF")}</button>
					<button class="btn btn-default btn-sm dgi-st-test">${__("Tester la connexion")}</button>
					<button class="btn btn-danger btn-sm dgi-st-stop" style="display:none">${__("Arreter le poste")}</button>
				</div>
			</div>
			<div class="dgi-st-step" style="margin-top:12px"></div>
			<div class="dgi-st-device" style="margin-top:12px"></div>`);
		$s.find(".dgi-st-gw").on("change", (e) => this.select(e.target.value));
		$s.find(".dgi-st-connect").on("click", () => this.connect_interactive());
		$s.find(".dgi-st-c1").on("click", () => this.read_status());
		$s.find(".dgi-st-c2").on("click", () => this.read_server());
		$s.find(".dgi-st-2b").on("click", () => this.read_taxpayer());
		$s.find(".dgi-st-propose").on("click", () => this.propose_device());
		$s.find(".dgi-st-test").on("click", () => this.test_connection());
		$s.find(".dgi-st-stop").on("click", () => this.stop_session());
	}

	async select(gateway_id, auto) {
		await this.stop_session();
		this.gw = this.stations.find((s) => s.name === gateway_id) || null;
		if (this.device && (!this.gw || !this.device_status || this.device_status.nid !== this.gw.mcf_device)) {
			await this.station_stop_device();
		}
		this.save_pref({ gateway_id: this.gw ? this.gw.name : "" });
		this.render_setup();
		this.render_cards();
		if (!this.gw) return;
		this.identity = await this.M.Identity.load(this.gw.name, this.keys);
		this.journal = await new this.M.IDBJournal(`dgi-mcf-journal-${this.gw.name}`).open();
		await this.journal.purge(30);
		if (auto && !this.device) await this.connect_known_port();
		this.render_probe();
		await this.next_step();
	}

	async next_step() {
		const $step = $(this.page.body).find(".dgi-st-step").empty();
		if (!this.gw) return;
		const fresh = (this.stations = await frappe.xcall("dgi_compliance.gateway.station.stations")).find((s) => s.name === this.gw.name);
		if (fresh) {
			this.gw = fresh;
			$(this.page.body).find(`.dgi-st-gw option[value="${CSS.escape(fresh.name)}"]`)
				.text(`${fresh.name} - ${fresh.point_of_sale} (${__(fresh.status)})`);
		}
		this.render_cards();
		const fp = this.identity ? await this.identity.fingerprint() : null;
		if (this.gw.status === "Active" && this.identity && fp === this.gw.key_fingerprint) {
			return this.start_session();
		}
		const enrol_form = `<div class="row"><div class="col-sm-4"><input class="form-control dgi-st-code" placeholder="XXXX-XXXX-XXXX" autocomplete="off"></div>
			<div class="col-sm-4"><button class="btn btn-primary btn-sm dgi-st-enrol">${__("Enroler ce poste")}</button></div></div>`;
		const bind_enrol = () => $step.find(".dgi-st-enrol").on("click", () => this.enrol($step.find(".dgi-st-code").val()));
		if (this.gw.status === "Active" && (!this.identity || fp !== this.gw.key_fingerprint)) {
			// v5.4.1: new PC, other browser or site data erased. The poste is re-enrolled HERE with a new code
			// (before, the page only said to revoke the poste, and a revoked poste cannot be enrolled again).
			$step.html(`<div class="alert alert-danger">${__("Ce poste est approuve avec la cle d'un autre navigateur (empreinte {0}) : ce navigateur ne peut pas l'utiliser tel quel.", [esc(this.gw.key_fingerprint)])}
				<div style="margin-top:6px">${__("Pour utiliser CE navigateur a sa place : 1) le responsable ouvre le poste {0} dans ERPNext, Passerelle > Generer un code d'enrolement ; 2) branchez le MCF et cliquez sur Connecter le MCF ; 3) saisissez le code ci-dessous. L'ancien navigateur cesse alors de fonctionner et le poste doit etre approuve de nouveau par une autre personne.", [esc(this.gw.name)])}</div></div>${enrol_form}`);
			bind_enrol();
			return;
		}
		if (this.gw.status === "A approuver") {
			const mine = fp && fp === this.gw.key_fingerprint;
			$step.html(`<div class="alert alert-${mine ? "info" : "danger"}">
				${mine ? __("En attente d'approbation. Empreinte de la cle de ce navigateur :") : __("La cle en attente d'approbation n'est PAS celle de ce navigateur. Prevenez le responsable fiscal.")}
				<div style="font-size:22px;letter-spacing:2px;font-weight:700;margin:8px 0">${esc(fp || this.gw.key_fingerprint)}</div>
				${__("Une autre personne que vous (DGI Fiscal Manager ou DGI Approbateur) compare cette empreinte dans ERPNext puis approuve le poste. Cette page demarre seule apres l'approbation.")}</div>`);
			clearTimeout(this.wait_timer);
			this.wait_timer = setTimeout(() => this.next_step(), 10000);
			return;
		}
		$step.html(`
			<div class="alert alert-info">${__("Enrolement de ce navigateur : 1) branchez le MCF et cliquez sur Connecter le MCF ; 2) saisissez le code d'enrolement communique par le responsable fiscal (valable 30 min, une seule fois).")}</div>
			${enrol_form}`);
		bind_enrol();
	}

	// ------------------------------------------------------------------ MCF connection
	async open_port(port) {
		const t = await new this.M.WebSerialTransport(port, { baudRate: (this.gw && this.gw.baudrate) || 115200 }).open();
		const dev = new this.M.MCFDevice(t, { trace: (d, b, l) => this.trace(d, b, l) });
		const st = await dev.status();
		return { dev, st };
	}
	async connect_interactive() {
		try {
			const port = await navigator.serial.requestPort({});
			await this.use_port(port);
		} catch (e) {
			if (e && e.name === "NotFoundError") return;      // dialog closed
			this.alert(esc(err_text(e)), "danger");
		}
	}
	async connect_known_port() {
		const dev = await this.reconnect();
		if (dev) this.set_device(dev);
	}
	async reconnect() {
		const expected = this.gw && this.gw.mcf_device;
		for (const port of await navigator.serial.getPorts()) {
			try {
				const { dev, st } = await this.open_port(port);
				if (!expected || st.nid === expected) {
					this.device_status = st;
					this.device = dev;
					this.show_device();
					this.log(__("MCF {0} reconnecte.", [st.mid]));
					return dev;
				}
				await dev.close();
			} catch (e) {
				try { await port.close(); } catch (x) { /* not open */ }
			}
		}
		return null;
	}
	async use_port(port) {
		if (this.device) { await this.station_stop_device(); }
		this.probe = null;
		const { dev, st } = await this.open_port(port);
		this.device_status = st;
		this.log(__("MCF connecte : MID {0}, NIF {1}, compteur total {2}, groupes {3}.", [st.mid, st.nif, st.tc, Object.keys(st.tax_rates).join("")]));
		if (this.gw && !this.gw.mcf_device) {
			// v5.3.0: no validated MCF yet for this point de vente -> read it and propose it (no fiscal effect)
			this.alert(__("Aucun MCF valide pour {0}. MCF lu : NID {1}. Cliquez sur 'Proposer ce MCF' : un responsable le validera dans ERPNext.",
				[esc(this.gw.point_of_sale), esc(st.nid)]), "info");
			this.device = dev;
			this.show_device();
			return;
		}
		if (this.gw && st.nid !== this.gw.mcf_device) {
			this.alert(__("Le MCF branche (NID {0}) n'est pas celui du point de vente {1} (NID {2}). Il ne sera pas utilise.",
				[esc(st.nid), esc(this.gw.point_of_sale), esc(this.gw.mcf_device)]), "danger");
			await dev.close();
			return;
		}
		this.alert("");
		this.set_device(dev);
	}
	set_device(dev) {
		this.device = dev;
		if (this.station) { this.station.setDevice(dev); this.station.wake(); }
		this.show_device();
		this.render_cards();
		this.render_probe();
	}
	async station_stop_device() {
		if (this.station) await this.station.dropDevice();
		else if (this.device) await this.device.close();
		this.device = null;
	}
	show_device() {
		const st = this.device_status;
		if (!st) return;
		const rates = Object.entries(st.tax_rates || {}).map(([k, v]) => `${k} ${v}%`).join(" · ");
		$(this.page.body).find(".dgi-st-device").html(`<table class="table table-condensed small" style="margin:0">
			<tr><th>MID</th><td>${esc(st.mid)}</td><th>NIF</th><td>${esc(st.nif)}</td><th>${__("Date MCF")}</th><td>${esc(st.dt)}</td></tr>
			<tr><th>${__("Compteurs (total / ventes / avoirs)")}</th><td>${esc(st.tc)} / ${esc(st.fvc)} / ${esc(st.frc)}</td>
				<th>${__("Derniere facture")}</th><td colspan="3">${esc(st.dfn)} ${esc(st.dft)} ${esc(st.dfdt)}</td></tr>
			<tr><th>${__("Groupes de taxation ({0})", [st.group_count])}</th><td colspan="5">${esc(rates)}</td></tr>
			<tr><th>${__("Etat")}</th><td colspan="5">${esc((st.flags || []).join(", ") || "OK")}${st.invoice_open ? " - " + __("facture ouverte") : ""}</td></tr></table>`);
		this.render_probe();
	}
	async need_device() {
		if (this.device) return this.device;
		frappe.msgprint(__("Connectez d'abord le MCF."));
		return null;
	}
	async read_status() {
		const dev = await this.need_device();
		if (!dev) return;
		try { this.device_status = await dev.lock(() => dev.status()); this.show_device(); this.log("C1h " + JSON.stringify(this.device_status)); }
		catch (e) { this.log("C1h : " + e.message); }
	}
	async read_server() {
		const dev = await this.need_device();
		if (!dev) return;
		try {
			const s = await dev.lock(() => dev.serverStatus());
			this.log(__("C2h : envoyees {0}, disponibles dans le MCF {1}, derniere connexion reussie {2}, etat {3} {4}", [s.sent, s.pending, s.last_sync, s.state, s.error]));
		} catch (e) { this.log("C2h : " + e.message); }
	}
	async read_taxpayer() {
		const dev = await this.need_device();
		if (!dev) return;
		for (const opt of ["I0", "I1", "I2", "I3", "I4"]) {
			try { this.log(`2Bh ${opt} : ${await dev.lock(() => dev.taxpayer(opt))}`); } catch (e) { this.log(`2Bh ${opt} : ${e.message}`); }
		}
	}

	// ------------------------------------------------------------------ v5.3.0 : decouverte et test (sans effet fiscal)
	async read_identity(dev) {
		return dev.lock(async () => {
			const st = await dev.status();
			const tp = {};
			for (const opt of ["I0", "I1", "I2", "I3", "I4"]) {
				try { tp[opt] = await dev.taxpayer(opt); } catch (e) { tp[opt] = ""; }
			}
			let srv = null;
			try { srv = await dev.serverStatus(); } catch (e) { srv = { read_error: e.message }; }   // v5.3.1: ER != read failure
			return { st, tp, srv };
		});
	}
	async propose_device() {
		if (!this.gw) { frappe.msgprint(__("Choisissez d'abord le poste MCF.")); return; }
		const dev = await this.need_device();
		if (!dev) return;
		try {
			const { st, tp, srv } = await this.read_identity(dev);
			this.device_status = st;
			this.show_device();
			const observation = {
				mid: st.mid, nid: st.nid, nif: st.nif, tc: st.tc, fvc: st.fvc, frc: st.frc, dt: st.dt,
				group_count: st.group_count, tax_rates: st.tax_rates, flags: st.flags || [],
				taxpayer_name: tp.I0 || "", taxpayer_address: [tp.I1, tp.I2].filter(Boolean).join(", "),
				taxpayer_phone: tp.I3 || "", taxpayer_email: tp.I4 || "", dgi: srv,
			};
			this.log("2Bh " + JSON.stringify(tp));
			const r = await frappe.xcall("dgi_compliance.gateway.station.propose_device",
				{ gateway_id: this.gw.name, observation: JSON.stringify(observation) });
			this.alert(__("MCF {0} propose (etat : {1}). Un responsable fiscal doit maintenant le valider dans ERPNext (DGI MCF Device > Valider ce MCF).",
				[esc(r.nid), esc(__(r.status))]), "success");
		} catch (e) {
			this.alert(esc(err_text(e)), "danger");
		}
	}
	// v5.3.2: one test for every case. With a poste holding the port, it reads through the open port;
	// otherwise the MCF is read on a port already allowed for the site (or chosen), then released.
	// ERPNext compares (NID, MID, NIF, rates, poste) and records the result on the MCF (TEST_CONNECTIVITE).
	async test_connection() {
		const D = window.DGIMCFDesk;
		const opts = { origin: this.gw ? __("Poste MCF {0}", [this.gw.name]) : __("Poste MCF (sans poste)"),
			gateway_id: this.gw ? this.gw.name : null, nid: (this.gw && this.gw.mcf_device) || null };
		let read = null, error = null, r;
		try {
			if (this.device) {
				const dev = this.device;
				try {
					read = await dev.lock(async () => {
						const t0 = performance.now();
						const st = await dev.status();
						const elapsed_ms = Math.round(performance.now() - t0), pc_epoch = Date.now();
						let srv;
						try { srv = await dev.serverStatus(); } catch (e) { srv = { read_error: e.message }; }
						return { st, srv, port: dev.port, elapsed_ms, pc_epoch };
					});
					this.device_status = read.st;
					this.show_device();
				} catch (e) {
					error = (e && e.message) || String(e);
				}
			} else {
				const problem = D.browser_problem();
				if (problem) { this.alert(esc(problem), "danger"); return; }
				try {
					read = (await D.choose_and_read(opts.nid)).read;
					this.probe = { read };
				} catch (e) {
					if (e && e.name === "NotFoundError") return;          // window closed without choosing
					if (!opts.nid) { this.alert(esc(err_text(e)), "danger"); return; }
					error = (e && e.message) || String(e);
				}
			}
			r = await D.record(error ? { error } : D.observation(read), opts);
		} catch (e) {
			this.alert(esc(err_text(e)), "danger");
			return;
		}
		this.log(__("Test de connexion : {0} - {1}", [r.result, r.summary]));
		const color = { OK: "success", Avertissement: "warning" }[r.result] || "danger";
		this.alert(`<b>${esc(__("Test de connexion du MCF {0} : {1}", [r.nid || "?", __(r.result)]))}</b>`
			+ `<div style="margin-top:8px">${D.checks_html(r.checks)}</div>`, color);
		if (this.probe && this.probe.read && r.known) {
			try { this.probe.status = await D.load_status({ nid: r.nid }); } catch (e) { /* no access */ }
		}
		this.render_probe();
		this.render_cards();
	}

	// ------------------------------------------------------------------ v5.3.2 : MCF branche, sans poste
	async probe_known(interactive) {
		if (this.device) return;                          // the poste holds the port
		const D = window.DGIMCFDesk;
		if (!D || D.browser_problem()) return;
		let found = null;
		try {
			found = interactive ? await D.choose_and_read(null) : await D.find_known(null);
		} catch (e) {
			if (e && e.name === "NotFoundError") return;   // window closed without choosing
			this.probe = { error: (e && e.message) || String(e) };
			this.render_probe(); this.render_cards();
			return;
		}
		if (!found) {
			if (interactive) this.probe = { error: __("Aucun MCF n'a repondu sur ce port.") };
			this.render_probe(); this.render_cards();
			return;
		}
		const st = found.read.st;
		this.probe = { read: found.read };
		this.log(__("MCF detecte sur ce PC : MID {0}, NIF {1}, port {2} (lecture sans effet fiscal).", [st.mid, st.nif, found.read.port]));
		try { this.probe.status = await D.load_status({ nid: st.nid }); } catch (e) { this.probe.status = null; }
		this.render_probe();
		this.render_cards();
	}

	render_probe() {
		const $card = $(this.page.body).find(".dgi-st-probe-card");
		const $p = $(this.page.body).find(".dgi-st-probe");
		if (this.device) { $card.hide(); return; }                 // shown in the poste section instead
		$card.show();
		const D = window.DGIMCFDesk;
		if (!D) return;
		const pr = this.probe;
		if (!pr || !pr.read) {
			$p.html(`<p class="text-muted">${esc((pr && pr.error) || __("Aucun MCF lu sur ce PC. Branchez le MCF puis cliquez sur 'Lire le MCF branche' et choisissez son port (USB Serial Device). Un port deja autorise pour ce site est relu automatiquement a l'ouverture de la page."))}</p>`);
			return;
		}
		const s = pr.status || {};
		const actions = [];
		if (s.known) {
			const mine = (this.stations || []).filter((g) => g.point_of_sale === s.point_of_sale && (!this.gw || this.gw.name !== g.name));
			for (const g of mine) actions.push(`<button class="btn btn-sm btn-default" data-use="${esc(g.name)}">${esc(__("Utiliser le poste {0}", [g.name]))}</button>`);
			if (s.can_create_station) actions.push(`<button class="btn btn-sm btn-primary dgi-st-create">${esc(__("Creer le poste MCF de {0}", [s.point_of_sale]))}</button>`);
			actions.push(`<a class="btn btn-sm btn-default" href="/app/dgi-mcf-device/${encodeURIComponent(s.nid)}">${esc(s.status === "Actif" ? __("Ouvrir la fiche MCF") : __("Ouvrir la fiche pour valider ce MCF"))}</a>`);
		} else if (s.can_manage) {
			actions.push(`<button class="btn btn-sm btn-primary dgi-st-probe-propose">${esc(__("Proposer ce MCF"))}</button>`);
		}
		const erp = s.known ? D.status_html(s)
			: `<div class="alert alert-warning">${esc(__("Ce MCF (NID {0}) n'existe pas dans ERPNext.", [pr.read.st.nid]))} ${esc(s.can_manage
				? __("Proposez-le : un responsable le validera ensuite.")
				: __("Un DGI Fiscal Manager doit le proposer (DGI Compliance Settings > MCF > Rechercher un MCF)."))}</div>`;
		$p.html(`<div class="row"><div class="col-md-6">${D.read_table(pr.read)}</div>
			<div class="col-md-6"><div class="text-muted small" style="margin-bottom:4px">${esc(__("Dans ERPNext (dernier etat enregistre ; cliquez sur Tester la connexion pour le mettre a jour)"))}</div>${erp}
			<div style="margin-top:8px;display:flex;gap:6px;flex-wrap:wrap">${actions.join("")}</div></div></div>`);
		$p.find("[data-use]").on("click", (e) => {
			const name = $(e.currentTarget).attr("data-use");
			$(this.page.body).find(".dgi-st-gw").val(name);
			this.select(name);
		});
		$p.find(".dgi-st-create").on("click", () => this.create_station(s.point_of_sale));
		$p.find(".dgi-st-probe-propose").on("click", () => this.propose_probe());
	}

	async create_station(point_of_sale) {
		try {
			const r = await frappe.xcall("dgi_compliance.fiscal.mcf_status.create_station", { point_of_sale });
			this.stations = await frappe.xcall("dgi_compliance.gateway.station.stations");
			this.log(__("Poste {0} : {1} pour {2}.", [r.name, r.created ? __("cree") : __("deja existant"), point_of_sale]));
			await this.select(r.name, true);
			this.alert(__("Poste MCF {0} {1}. Etape suivante : un responsable ouvre {2} > Passerelle > Generer un code d'enrolement et le communique de vive voix ; saisissez-le ci-dessus, puis le responsable approuve le poste avec l'empreinte affichee.",
				[esc(r.name), r.created ? __("cree") : __("deja existant"),
					`<a href="/app/dgi-gateway/${encodeURIComponent(r.name)}">${esc(r.name)}</a>`]), "info");
		} catch (e) {
			this.alert(esc(err_text(e)), "danger");
		}
	}

	async propose_probe() {
		const pr = this.probe;
		if (!pr || !pr.read) return;
		try {
			const r = await frappe.xcall("dgi_compliance.fiscal.registry.propose_from_browser",
				{ observation: JSON.stringify(window.DGIMCFDesk.observation(pr.read)) });
			this.alert(__("MCF {0} propose (etat : {1}). Un responsable fiscal doit maintenant le valider (DGI MCF Device > Valider ce MCF).", [esc(r.nid), esc(__(r.status))]), "success");
			this.probe.status = await window.DGIMCFDesk.load_status({ nid: r.nid });
			this.render_probe();
		} catch (e) {
			this.alert(esc(err_text(e)), "danger");
		}
	}

	// ------------------------------------------------------------------ enrolment
	async enrol(code) {
		code = (code || "").trim().toUpperCase();
		if (!code) return frappe.msgprint(__("Saisissez le code d'enrolement."));
		const dev = await this.need_device();
		if (!dev) return;
		try {
			const st = await dev.lock(() => dev.status());
			if (st.nid !== this.gw.mcf_device) {
				return frappe.msgprint(__("Le MCF branche (NID {0}) n'est pas celui du point de vente (NID {1}).", [esc(st.nid), esc(this.gw.mcf_device)]));
			}
			// a NEW non-extractable key for this enrolment (the old one, if any, is replaced)
			this.identity = await this.M.Identity.create(this.gw.name, this.keys);
			const res = await frappe.xcall("dgi_compliance.gateway.station.enroll_station", {
				gateway_id: this.gw.name, code, public_key: this.identity.publicKeyB64,
				device: { mid: st.mid, nif: st.nif }, host: this.M.hostInfo(),
			});
			const fp = await this.identity.fingerprint();
			if (res.fingerprint !== fp) throw new Error(__("Empreinte renvoyee par le serveur differente de celle de ce navigateur."));
			this.log(__("Poste enrole, empreinte {0}. En attente d'approbation.", [fp]));
			await this.next_step();
		} catch (e) {
			this.alert(esc(err_text(e, __("Enrolement refuse : lisez le message affiche. Si le code est mal saisi, expire (30 min) ou deja utilise, demandez un nouveau code au responsable."))), "danger");
		}
	}

	// ------------------------------------------------------------------ service
	async start_session() {
		const $btn = $(this.page.body).find(".dgi-st-stop");
		let res;
		try {
			res = await frappe.xcall("dgi_compliance.gateway.station.open_session", { gateway_id: this.gw.name });
		} catch (e) { this.alert(esc(err_text(e)), "danger"); return; }
		if (!res.operator_token) { this.next_step(); return; }
		this.session = res;
		this.identity.clockOffsetS = res.server_epoch - Date.now() / 1000;
		if (Math.abs(this.identity.clockOffsetS) > 120)
			this.log(__("Horloge de ce poste decalee de {0} s par rapport au serveur (corrige pour les signatures).", [Math.round(this.identity.clockOffsetS)]));
		const server = new this.M.ServerClient(window.location.origin, this.identity, { operatorToken: res.operator_token });
		const cfg = Object.assign({}, res.config);
		this.station = new this.M.Station({
			server, journal: this.journal, device: this.device, config: cfg,
			onState: () => {
				if (!this.station) return;
				if (this.station.state.server === "connecte") this.auth_failures = 0;
				this.device = this.station.dev;
				if (this.station.lastStatus && this.station.lastStatus !== this.device_status) {
					this.device_status = this.station.lastStatus;
					this.show_device();
				}
				this.render_cards();
			},
			log: (m) => this.log(m), reconnect: () => this.reconnect(),
			onAuthError: (e) => this.recover_session(e),
		});
		this.auth_failures = 0;
		$btn.show();
		this.alert("");
		this.log(__("Poste {0} demarre (operateur {1}).", [this.gw.name, frappe.session.user]));
		this.renew_timer = setInterval(() => this.renew_token(), 6 * 3600 * 1000);
		this.queue_timer = setInterval(() => this.refresh_queue(), 30000);
		this.refresh_queue();
		this.station.run();
	}
	// v5.4.1: ERPNext refused the poste (HTTP 401 / 403). Most often the operator token expired or was lost
	// (PC asleep more than 12 h, server restarted): a new one is asked with the cashier's session and the poste
	// goes on by itself. Before, the poste retried for ever and the invoices waited until the page was reloaded.
	async recover_session(err) {
		if (!this.station || this.recovering) return false;
		this.recovering = true;
		try {
			this.auth_failures = (this.auth_failures || 0) + 1;       // back to 0 at the next successful cycle (onState)
			let res;
			try {
				res = await frappe.xcall("dgi_compliance.gateway.station.open_session", { gateway_id: this.gw.name });
			} catch (e) {
				this.alert(__("Le poste MCF est arrete : votre session ERPNext n'est plus valable ou le serveur ne repond pas. Reconnectez-vous a ERPNext dans ce navigateur puis rouvrez la page Poste MCF. Les factures destinees au MCF attendent."), "danger");
				return false;
			}
			const same_key = !res.fingerprint || res.fingerprint === (await this.identity.fingerprint());
			if (res.operator_token && same_key && this.auth_failures <= 2) {
				this.station.server.operatorToken = res.operator_token;
				this.session = res;
				this.identity.clockOffsetS = res.server_epoch - Date.now() / 1000;
				this.log(__("Session du poste renouvelee ({0}).", [String((err && err.message) || "").slice(0, 120)]));
				this.alert("");
				return true;
			}
			// poste revoked, key replaced by another browser, waiting for approval, or refusals that persist
			const why = res.operator_token ? String((err && err.message) || "") : __("poste a l'etat {0}", [__(res.status || "?")]);
			await this.stop_session();
			this.alert(__("Le poste MCF est arrete : ERPNext refuse ce poste ({0}). Les factures destinees au MCF attendent ou partent en secours. Suivez l'indication ci-dessous ou prevenez le responsable fiscal.", [esc(why)]), "danger");
			if (!res.operator_token || !same_key) await this.next_step();
			return false;
		} finally { this.recovering = false; }
	}
	async renew_token() {
		if (!this.station) return;
		try {
			const res = await frappe.xcall("dgi_compliance.gateway.station.open_session", { gateway_id: this.gw.name });
			if (res.operator_token) {
				const old = this.station.server.operatorToken;
				this.station.server.operatorToken = res.operator_token;
				frappe.xcall("dgi_compliance.gateway.station.close_session", { gateway_id: this.gw.name, operator_token: old }).catch(() => {});
			}
		} catch (e) { this.log(__("Renouvellement du jeton operateur impossible : {0}", [err_text(e)])); }
	}
	async refresh_queue() {
		if (!this.gw) return;
		try {
			const r = await frappe.xcall("dgi_compliance.fiscal.api.device_status", { point_of_sale: this.gw.point_of_sale });
			this.queue_info = r.operations_en_cours;
			this.render_cards();
		} catch (e) { /* no access */ }
	}
	async stop_session(unloading) {
		clearInterval(this.renew_timer); clearInterval(this.queue_timer); clearTimeout(this.wait_timer);
		if (this.station) {
			this.station.stop();
			const token = this.station.server.operatorToken;
			this.station = null;
			if (this.gw && token) {
				const args = { gateway_id: this.gw.name, operator_token: token };
				if (unloading) {
					fetch("/api/method/dgi_compliance.gateway.station.close_session", { method: "POST", keepalive: true,
						headers: { "Content-Type": "application/json", "X-Frappe-CSRF-Token": frappe.csrf_token },
						body: JSON.stringify(args) }).catch(() => {});
				} else {
					await frappe.xcall("dgi_compliance.gateway.station.close_session", args).catch(() => {});
					this.log(__("Poste arrete."));
				}
			}
		}
		$(this.page.body).find(".dgi-st-stop").hide();
		this.render_cards();
	}

	export_diagnostic() {
		const st = this.station ? this.station.state : {};
		const data = {
			exported_at: new Date().toISOString(), version: this.M.VERSION, user: frappe.session.user,
			station: this.gw ? { id: this.gw.name, point_of_sale: this.gw.point_of_sale, nid: this.gw.mcf_device, status: this.gw.status } : null,
			browser: this.M.hostInfo(), state: st, device_status: this.device_status || null,
			probe: this.probe && this.probe.read ? { st: this.probe.read.st, srv: this.probe.read.srv, port: this.probe.read.port } : null,
			log: this.logs, frames: this.frames,
			notice: "Contient les trames echangees avec le MCF (dont les lignes de factures). A transmettre uniquement au support.",
		};
		const blob = new Blob([JSON.stringify(data, null, 1)], { type: "application/json" });
		const a = document.createElement("a");
		a.href = URL.createObjectURL(blob);
		a.download = `diagnostic-poste-mcf-${(this.gw && this.gw.name) || "x"}-${Date.now()}.json`;
		a.click();
		setTimeout(() => URL.revokeObjectURL(a.href), 5000);
	}
}
