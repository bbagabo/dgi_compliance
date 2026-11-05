/*
 * End-to-end test v5.3.2 in a real Chromium (Playwright): an MCF already validated (Actif) for a point de
 * vente WITHOUT any poste - the situation met on 05/11/2026 with the physical MCF ED02000853.
 *   1. page Poste MCF: the MCF plugged into this PC (port already allowed for the site) is detected and shown
 *      without any poste, with its state in ERPNext ("aucun poste");
 *   2. "Tester la connexion": C1h + C2h, compared by ERPNext, recorded on the MCF (TEST_CONNECTIVITE);
 *   3. "Creer le poste MCF de ...": DGI Gateway "Poste navigateur" created (state Nouvelle), enrolment step;
 *   4. fiche DGI MCF Device: "Statut MCF" block + "Tester la connexion (sur ce PC)";
 *   5. DGI Compliance Settings > Rechercher un MCF: an MCF already active is not proposed again, the reading is
 *      recorded as a connection test;
 *   6. DGI Point of Sale: "Statut MCF" block.
 * The MCF is the protocol simulator (dgi_gateway) behind a fake navigator.serial. No fiscal command is sent.
 *
 *   node e2e_statut_mcf.js <site_url>
 * Requires: bench serve on <site_url>, `bench execute dgi_compliance.tests.e2e_station.prepare_statut`,
 * PYTHON (bench env), BENCH and SITE_NAME env vars. TEST SITES ONLY.
 */
"use strict";
const path = require("node:path");
const { spawn, execFileSync } = require("node:child_process");
const readline = require("node:readline");
const { chromium } = require("playwright");

const [, , SITE] = process.argv;
const BENCH = process.env.BENCH || "/home/frappe/frappe-bench";
const SITE_NAME = process.env.SITE_NAME || "test.local";
const MANAGER = "responsable.e2e@example.com", PASSWORD = "Respo#E2E-2026!";
const NID = "STE0000001", PVF = "E2E STATUT";
const SHOT = process.env.SHOT || "/tmp/statut_mcf.png";

const py = spawn(process.env.PYTHON || "python3", [path.join(__dirname, "sim_bridge.py")], { stdio: ["pipe", "pipe", "inherit"] });
const rl = readline.createInterface({ input: py.stdout });
const waiting = [];
rl.on("line", (l) => waiting.shift()(JSON.parse(l)));
const ask = (req) => new Promise((r) => { waiting.push(r); py.stdin.write(JSON.stringify(req) + "\n"); });

function bench(method, kwargs) {
	const args = ["--site", SITE_NAME, "execute", method];
	if (kwargs) args.push("--kwargs", JSON.stringify(kwargs));
	const cmd = `[ -f ~/env.sh ] && . ~/env.sh; cd ${BENCH} && bench ${args.map((a) => `'${a}'`).join(" ")} 2>/dev/null`;
	const out = execFileSync("su", ["-", "frappe", "-c", cmd]).toString();
	return out.trim().split("\n").pop();
}
const state = () => JSON.parse(bench("dgi_compliance.tests.e2e_station.statut_state").replace(/^STATE=/, ""));
const check = (cond, msg) => { if (!cond) throw new Error("ECHEC : " + msg); console.log("OK  " + msg); };

// Fake navigator.serial: ONE port already allowed for the site (getPorts), like COM8 after a first search.
const FAKE_SERIAL = () => {
	class FakePort {
		getInfo() { return { usbVendorId: 0x03eb, usbProductId: 0x6119 }; }
		async open() {
			if (this.opened) throw new Error("Failed to open serial port: the port is already open.");
			this.opened = true;
			const self = this;
			this.readable = new ReadableStream({ start(c) { self.ctrl = c; } });
			this.writable = new WritableStream({
				async write(chunk) {
					const hex = Array.from(chunk, (b) => b.toString(16).padStart(2, "0")).join("");
					const out = await window.__mcfWrite(hex);
					if (out) self.ctrl.enqueue(Uint8Array.from(out.match(/../g).map((h) => parseInt(h, 16))));
				},
			});
		}
		async close() { this.opened = false; try { this.ctrl.close(); } catch (e) { /* closed */ } }
	}
	const port = new FakePort();
	window.__opens = () => port.opened;
	Object.defineProperty(navigator, "serial", { configurable: true, value: {
		requestPort: async () => port, getPorts: async () => [port],
		addEventListener() {}, removeEventListener() {},
	} });
};
const visibleModal = (page) => page.locator(".modal.show").last();
async function clickGroupItem(page, group, label) {
	const ok = await page.evaluate(([g, l]) => {
		const a = Array.from(document.querySelectorAll(`.inner-group-button[data-label="${g}"] .dropdown-item`))
			.find((x) => x.textContent.trim() === l);
		if (a) a.click();
		return Boolean(a);
	}, [group, label]);
	if (!ok) throw new Error(`menu ${group} > ${label} introuvable`);
}

(async () => {
	await ask({ op: "new", id: "st", nid: NID, nif: "A0700190Q", rates: { A: 0, B: 16, C: 8, F: 16, G: 8 } });
	await ask({ op: "fault", id: "st", name: "server_message", value: "Operation terminee!" });
	const browser = await chromium.launch({ args: [`--unsafely-treat-insecure-origin-as-secure=${SITE}`] });
	const ctx = await browser.newContext({ viewport: { width: 1366, height: 900 } });
	const page = await ctx.newPage();
	page.on("pageerror", (e) => console.log("[page error]", e.message));
	global.__page = page;
	await page.exposeFunction("__mcfWrite", async (hex) => (await ask({ op: "write", id: "st", hex })).hex || "");
	await page.addInitScript(FAKE_SERIAL);
	const login = await page.request.post(`${SITE}/api/method/login`, { data: { usr: MANAGER, pwd: PASSWORD } });
	if (login.status() !== 200) throw new Error("login " + login.status());

	// 1. Poste MCF without any poste: the MCF plugged into this PC is detected and shown
	await page.goto(`${SITE}/app/dgi-mcf-station`);
	await page.waitForFunction(() => /Detecte sur ce PC/.test(document.querySelector(".dgi-st-cards")?.innerText || ""), null, { timeout: 30000 });
	const probe = await page.textContent(".dgi-st-probe");
	check(probe.includes(NID + "-1") && probe.includes("A0700190Q"), "MCF detecte sans poste (port deja autorise) : MID et NIF lus");
	check(/Actif/.test(probe) && /aucun poste/.test(probe), "etat ERPNext affiche : Actif, aucun poste pour le point de vente");
	check(/CON \(connecte/.test(probe) && /Derniere connexion reussie/.test(probe), "connexion du MCF a la DGI (C2h) affichee");
	check(!(await page.evaluate(() => window.__opens())), "port libere apres la lecture (pas de blocage des autres pages)");
	check(await page.locator(".dgi-st-create").count() === 1, `bouton « Creer le poste MCF de ${PVF} » propose au responsable`);
	await page.screenshot({ path: SHOT.replace(".png", "-1-poste-sans-poste.png"), fullPage: true });

	// 2. connection test without poste
	await page.click(".dgi-st-probe-test");
	await page.waitForFunction(() => /Test de connexion du MCF/.test(document.querySelector(".dgi-st-alert").innerText), null, { timeout: 20000 });
	const alert1 = await page.textContent(".dgi-st-alert");
	check(/Avertissement/.test(alert1) && /Aucun poste MCF/.test(alert1), "test : identite conforme, avertissement « aucun poste »");
	let s = state();
	check(s.device.connection_status === "Avertissement" && /Poste MCF \(sans poste\)/.test(s.device.last_test_origin || ""),
		`resultat enregistre sur le MCF (${s.device.connection_status}, ${s.device.last_test_origin})`);
	check(s.device.test_dgi_state === "CON" && s.device.test_dgi_unsent === "0" && s.tests >= 1, "etat DGI, transactions non envoyees (DC - EC) et evenement TEST_CONNECTIVITE");
	await page.screenshot({ path: SHOT.replace(".png", "-2-test.png"), fullPage: true });

	// 3. the fiscal manager creates the poste of the point de vente
	await page.click(".dgi-st-create");
	await page.waitForSelector(".dgi-st-code", { timeout: 20000 });
	s = state();
	check(s.stations.length === 1 && s.stations[0].name === "POSTE-" + NID && s.stations[0].status === "Nouvelle"
		&& s.stations[0].kind === "Poste navigateur", `poste ${s.stations[0] && s.stations[0].name} cree (Poste navigateur, Nouvelle)`);
	check(await page.inputValue(".dgi-st-gw") === "POSTE-" + NID, "le poste cree est selectionne ; etape d'enrolement affichee");
	await page.screenshot({ path: SHOT.replace(".png", "-3-poste-cree.png"), fullPage: true });

	// 4. fiche MCF: Statut MCF block and test from this PC
	await page.goto(`${SITE}/app/dgi-mcf-device/${NID}`);
	await page.waitForFunction(() => /Dernier test de connexion/.test(document.querySelector('[data-fieldname="status_html"]')?.innerText || ""), null, { timeout: 30000 });
	const block = await page.textContent('[data-fieldname="status_html"]');
	check(block.includes("POSTE-" + NID) && /Avertissement/.test(block), "fiche MCF : bloc Statut MCF (dernier test, poste, connexion DGI)");
	await page.click('button:has-text("Tester la connexion (sur ce PC)")');
	await page.waitForFunction(() => /Test de connexion du MCF/.test(document.querySelector(".modal.show")?.innerText || ""), null, { timeout: 20000 });
	const dlg = await visibleModal(page).innerText();
	check(/NIF/.test(dlg) && /MID/.test(dlg) && /Groupes de taxation/.test(dlg), "fiche MCF : resultat detaille (NIF, MID, groupes...)");
	s = state();
	check(/Fiche MCF/.test(s.device.last_test_origin || ""), "fiche MCF : test enregistre avec son origine");
	await page.screenshot({ path: SHOT.replace(".png", "-4-fiche.png"), fullPage: false });
	await page.keyboard.press("Escape");

	// 5. settings search on an MCF already active: recorded as a test, not proposed again
	await page.goto(`${SITE}/app/dgi-compliance-settings`);
	await page.waitForSelector('.inner-group-button[data-label="MCF"]', { timeout: 30000 });
	await clickGroupItem(page, "MCF", "Rechercher un MCF (sur ce PC)");
	await page.waitForFunction(() => /MCF lu sur ce PC/.test(document.body.innerText), null, { timeout: 30000 });
	const m = visibleModal(page);
	check(/existe deja dans ERPNext \(etat : Actif\)/.test(await m.innerText()), "recherche : MCF deja actif reconnu");
	check(await m.locator(".btn-modal-primary").innerText() === "Enregistrer ce test de connexion", "recherche : « Enregistrer ce test de connexion » au lieu de « Proposer ce MCF »");
	await m.locator(".btn-modal-primary").click();
	await page.waitForFunction(() => /Test de connexion du MCF/.test(document.querySelector(".modal.show")?.innerText || ""), null, { timeout: 20000 });
	s = state();
	check(/Parametres/.test(s.device.last_test_origin || "") && s.device.status === "Actif", "recherche : test enregistre, MCF toujours actif");
	await page.keyboard.press("Escape");

	// 6. point de vente: Statut MCF block
	await page.goto(`${SITE}/app/dgi-point-of-sale/${encodeURIComponent(PVF)}`);
	await page.waitForFunction(() => /Dernier test de connexion/.test(document.querySelector('[data-fieldname="mcf_status_html"]')?.innerText || ""), null, { timeout: 30000 });
	check(true, "point de vente : bloc Statut MCF affiche");
	await page.screenshot({ path: SHOT.replace(".png", "-6-point-de-vente.png"), fullPage: true });

	const sim = await ask({ op: "state", id: "st" });
	check(sim.invoices.length === 0 && sim.tc === 0, "aucune facture sur le MCF : lectures sans effet fiscal");
	await browser.close();
	py.kill();
	console.log("E2E STATUT MCF : TOUT EST OK");
})().catch(async (e) => {
	console.error(e);
	try {
		await global.__page.screenshot({ path: SHOT.replace(".png", "-echec.png"), fullPage: true });
		console.log("[page]", (await global.__page.innerText("body")).slice(0, 2000));
	} catch (x) { /* no page */ }
	py.kill(); process.exit(1);
});
