// Page "Poste MCF" (DGI Compliance v5.2.0): the cash desk where the MCF is plugged becomes the
// gateway of its MCF while this page is open in Chrome / Edge. See public/js/dgi_mcf.js.
frappe.pages["dgi-mcf-station"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({ parent: wrapper, title: __("Poste MCF"), single_column: true });
	frappe.require("/assets/dgi_compliance/js/dgi_mcf.js", () => {
		wrapper.dgi_station = new DGIStationPage(page, wrapper);
	});
};

frappe.pages["dgi-mcf-station"].on_page_show = function (wrapper) {
	if (wrapper.dgi_station) wrapper.dgi_station.refresh_queue();
};

const STORE_KEY = "dgi-mcf-station";
const esc = (s) => frappe.utils.escape_html(s === undefined || s === null ? "" : String(s));

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
		this.render_cards();
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
		const cards = [
			this.card(__("MCF"), this.device ? __("Connecte") : __("Non connecte"), this.device ? "green" : "red",
				dev.mid ? `${dev.mid} - NIF ${dev.nif}` : __("Brancher le MCF puis cliquer sur Connecter")),
			this.card(__("Poste"), this.gw ? __(this.gw.status) : __("Non choisi"), this.gw && this.gw.status === "Active" ? "green" : "orange",
				this.gw ? `${this.gw.name} - ${this.gw.point_of_sale}` : ""),
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
			this.alert(esc(e.message || e), "danger");
			return [];
		});
		this.render_setup();
		navigator.serial.addEventListener("connect", () => { if (this.station) this.station.wake(); });
		navigator.serial.addEventListener("disconnect", () => this.log(__("MCF debranche.")));
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
	}

	render_setup() {
		const $s = $(this.page.body).find(".dgi-st-setup").empty();
		if (!this.stations.length) {
			$s.html(`<p>${__("Aucun poste MCF n'est prevu pour vos points de vente. Un DGI Fiscal Manager doit creer une passerelle de type 'Poste navigateur' (DGI Gateway) et vous communiquer son code d'enrolement.")}</p>`);
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
		if (this.gw.status === "Active" && (!this.identity || fp !== this.gw.key_fingerprint)) {
			$step.html(`<div class="alert alert-danger">${__("Ce poste est approuve avec une autre cle (empreinte {0}). Ce navigateur ne peut pas l'utiliser : faites revoquer le poste et enrolez ce navigateur avec un nouveau code.", [esc(this.gw.key_fingerprint)])}</div>`);
			return;
		}
		if (this.gw.status === "A approuver") {
			const mine = fp && fp === this.gw.key_fingerprint;
			$step.html(`<div class="alert alert-${mine ? "info" : "danger"}">
				${mine ? __("En attente d'approbation. Empreinte de la cle de ce navigateur :") : __("La cle en attente d'approbation n'est PAS celle de ce navigateur. Prevenez le responsable fiscal.")}
				<div style="font-size:22px;letter-spacing:2px;font-weight:700;margin:8px 0">${esc(fp || this.gw.key_fingerprint)}</div>
				${__("Le responsable (DGI Fiscal Manager) compare cette empreinte dans ERPNext puis approuve le poste. Cette page demarre seule apres l'approbation.")}</div>`);
			clearTimeout(this.wait_timer);
			this.wait_timer = setTimeout(() => this.next_step(), 10000);
			return;
		}
		$step.html(`
			<div class="alert alert-info">${__("Enrolement de ce navigateur : 1) branchez le MCF et cliquez sur Connecter le MCF ; 2) saisissez le code d'enrolement communique par le responsable fiscal (valable 30 min, une seule fois).")}</div>
			<div class="row"><div class="col-sm-4"><input class="form-control dgi-st-code" placeholder="XXXX-XXXX-XXXX" autocomplete="off"></div>
			<div class="col-sm-4"><button class="btn btn-primary btn-sm dgi-st-enrol">${__("Enroler ce poste")}</button></div></div>`);
		$step.find(".dgi-st-enrol").on("click", () => this.enrol($step.find(".dgi-st-code").val()));
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
			this.alert(esc(e.message || e), "danger");
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
			this.log(__("C2h : envoyees {0}, en attente {1}, derniere synchro {2}, etat {3} {4}", [s.sent, s.pending, s.last_sync, s.state, s.error]));
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
			try { srv = await dev.serverStatus(); } catch (e) { srv = { error: e.message }; }
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
			this.alert(esc((e && e.message) || String(e)), "danger");
		}
	}
	async test_connection() {
		if (!this.gw || !this.gw.mcf_device) { frappe.msgprint(__("Aucun MCF valide pour ce poste.")); return; }
		const dev = await this.need_device();
		if (!dev) return;
		let result;
		try {
			const [st, srv] = await dev.lock(async () => [await dev.status(), await dev.serverStatus()]);
			const same = st.nid === this.gw.mcf_device;
			result = { ok: same, mid: st.mid, tc: st.tc, dgi: srv,
				summary: same ? __("C1h OK (MID {0}, compteur {1}) ; C2h : envoyees {2}, en attente {3}, derniere synchro {4}",
					[st.mid, st.tc, srv.sent, srv.pending, srv.last_sync])
					: __("MCF branche {0} au lieu de {1}", [st.nid, this.gw.mcf_device]) };
		} catch (e) {
			result = { ok: false, summary: (e && e.message) || String(e) };
		}
		this.log(__("Test de connexion : {0}", [result.summary]));
		try {
			await frappe.xcall("dgi_compliance.gateway.station.connectivity_test",
				{ gateway_id: this.gw.name, result: JSON.stringify(result) });
		} catch (e) { this.log("connectivity_test : " + ((e && e.message) || e)); }
		this.alert(esc(result.summary), result.ok ? "success" : "danger");
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
			this.alert(esc(e.message || e), "danger");
		}
	}

	// ------------------------------------------------------------------ service
	async start_session() {
		const $btn = $(this.page.body).find(".dgi-st-stop");
		let res;
		try {
			res = await frappe.xcall("dgi_compliance.gateway.station.open_session", { gateway_id: this.gw.name });
		} catch (e) { this.alert(esc(e.message || e), "danger"); return; }
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
				this.device = this.station.dev;
				if (this.station.lastStatus && this.station.lastStatus !== this.device_status) {
					this.device_status = this.station.lastStatus;
					this.show_device();
				}
				this.render_cards();
			},
			log: (m) => this.log(m), reconnect: () => this.reconnect(),
		});
		$btn.show();
		this.alert("");
		this.log(__("Poste {0} demarre (operateur {1}).", [this.gw.name, frappe.session.user]));
		this.renew_timer = setInterval(() => this.renew_token(), 6 * 3600 * 1000);
		this.queue_timer = setInterval(() => this.refresh_queue(), 30000);
		this.refresh_queue();
		this.station.run();
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
		} catch (e) { this.log(__("Renouvellement du jeton operateur impossible : {0}", [e.message || e])); }
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
