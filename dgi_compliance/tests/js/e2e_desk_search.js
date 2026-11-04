/*
 * End-to-end test of "Rechercher un MCF (sur ce PC)" (v5.3.1) in a real Chromium (Playwright):
 *   fiscal manager login -> DGI Compliance Settings -> MCF > Rechercher un MCF (sur ce PC)
 *   -> serial port = MCF simulator (in place of COM8) -> C1h, 2Bh, C2h read, no fiscal effect
 *   -> dialog with NID, NIF (matched with the company), counters -> "Proposer ce MCF"
 *   -> MCF 'Detecte' in ERPNext, never active, nothing linked to the point de vente.
 * Also checked: a port that does not answer, and a browser without Web Serial (clear messages).
 *
 *   node e2e_desk_search.js <site_url>
 * Requires: bench serve on <site_url>, `bench execute dgi_compliance.tests.e2e_station.prepare_desk_search`,
 * PYTHON (bench env), BENCH and SITE_NAME (default test.localhost) env vars. TEST SITES ONLY.
 */
"use strict";
const path = require("node:path");
const { spawn, execFileSync } = require("node:child_process");
const readline = require("node:readline");
const { chromium } = require("playwright");

const [, , SITE] = process.argv;
const BENCH = process.env.BENCH || "/home/frappe/frappe-bench";
const SITE_NAME = process.env.SITE_NAME || "test.localhost";
const MANAGER = "responsable.e2e@example.com", PASSWORD = "Respo#E2E-2026!";
const NID = "DSK0000001", PVF = "E2E RECHERCHE";
const SHOT = process.env.SHOT || "/tmp/recherche_mcf.png";

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
const state = () => JSON.parse(bench("dgi_compliance.tests.e2e_station.desk_search_state").replace(/^STATE=/, ""));
const check = (cond, msg) => { if (!cond) throw new Error("ECHEC : " + msg); console.log("OK  " + msg); };

// Fake navigator.serial: the bytes go to the Python MCF simulator (or nowhere when window.__mcfMute).
const FAKE_SERIAL = () => {
	class FakePort {
		getInfo() { return { usbVendorId: 0x1a86, usbProductId: 0x7523 }; }
		async open() {
			const self = this;
			this.readable = new ReadableStream({ start(c) { self.ctrl = c; } });
			this.writable = new WritableStream({
				async write(chunk) {
					if (window.__mcfMute) return;
					const hex = Array.from(chunk, (b) => b.toString(16).padStart(2, "0")).join("");
					const out = await window.__mcfWrite(hex);
					if (out) self.ctrl.enqueue(Uint8Array.from(out.match(/../g).map((h) => parseInt(h, 16))));
				},
			});
		}
		async close() { try { this.ctrl.close(); } catch (e) { /* closed */ } }
	}
	Object.defineProperty(navigator, "serial", { configurable: true, value: {
		requestPort: async () => new FakePort(), getPorts: async () => [],
		addEventListener() {}, removeEventListener() {},
	} });
};

async function openSettings(page) {
	await page.goto(`${SITE}/app/dgi-compliance-settings`);
	await page.waitForSelector('.inner-group-button[data-label="MCF"]', { timeout: 30000 });
}
async function search(page) {
	// the group menu of the form toolbar (the item is clicked through the DOM: the dropdown can be collapsed)
	const ok = await page.evaluate(() => {
		const a = Array.from(document.querySelectorAll('.inner-group-button[data-label="MCF"] .dropdown-item'))
			.find((x) => x.textContent.trim() === "Rechercher un MCF (sur ce PC)");
		if (a) a.click();
		return Boolean(a);
	});
	if (!ok) throw new Error("menu MCF > Rechercher un MCF (sur ce PC) introuvable");
}
const visibleModal = (page) => page.locator(".modal.show").last();

(async () => {
	await ask({ op: "new", id: "desk", nid: NID, nif: "A0700190Q", rates: { A: 0, B: 16, C: 5, O: 1, P: 1 } });
	const browser = await chromium.launch({ args: [`--unsafely-treat-insecure-origin-as-secure=${SITE}`] });
	const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
	const page = await ctx.newPage();
	page.on("pageerror", (e) => console.log("[page error]", e.message));
	global.__page = page;
	await page.exposeFunction("__mcfWrite", async (hex) => (await ask({ op: "write", id: "desk", hex })).hex || "");
	await page.addInitScript(FAKE_SERIAL);

	const login = await page.request.post(`${SITE}/api/method/login`, { data: { usr: MANAGER, pwd: PASSWORD } });
	if (login.status() !== 200) throw new Error("login " + login.status());

	// 1. a port that does not answer (another USB device, wrong COM): clear message, nothing recorded
	await openSettings(page);
	check(await page.locator('.inner-group-button[data-label="MCF"] .dropdown-item:has-text("Rechercher un MCF (sur ce PC)")').count() === 1,
		"bouton « Rechercher un MCF (sur ce PC) » present dans le menu MCF");
	await page.evaluate(() => { window.__mcfMute = true; });
	await search(page);
	await page.waitForFunction(() => /Aucun MCF lu sur ce port/.test(document.body.innerText), null, { timeout: 30000 });
	check(/ne repond pas comme un MCF/.test(await visibleModal(page).innerText()), "port muet : message « ne repond pas comme un MCF »");
	check(!state().device.status, "port muet : aucune fiche MCF creee");
	await page.keyboard.press("Escape");
	await page.waitForTimeout(500);

	// 2. the MCF (simulator in place of COM8): read, shown, proposed. Like the MCF tested on 04/11/2026, the
	//    simulator adds the optional ER field to C2h ("Operation terminee!"): the state must still be shown.
	await ask({ op: "fault", id: "desk", name: "server_message", value: "Operation terminee!" });
	await page.evaluate(() => { window.__mcfMute = false; });
	await search(page);
	await page.waitForFunction(() => /MCF lu sur ce PC/.test(document.body.innerText), null, { timeout: 30000 });
	const dlg = await visibleModal(page).innerText();
	check(dlg.includes(NID + "-1") && dlg.includes(NID), `NID et MID lus par le navigateur (C1h) : ${NID}-1`);
	check(dlg.includes("A0700190Q") && dlg.includes("DGI Test SARL"), "NIF lu et rapproche de la societe DGI Test SARL");
	check(/Nom : DGI TEST SARL/.test(dlg) && /Ville : Kinshasa/.test(dlg) && /Telephone : \+243/.test(dlg), "contribuable lu en 2Bh, champ par champ (nom, ville, telephone)");
	check(/Connexion DGI \(C2h\)/.test(dlg) && /CON \(connecte/.test(dlg) && /Derniere connexion reussie/.test(dlg),
		"etat de connexion DGI lu (C2h) : etat, transactions et derniere connexion reussie affiches");
	check(/Dernier message du MCF : Operation terminee!/.test(dlg), "champ ER de C2h affiche comme message du MCF, pas comme une erreur de lecture");
	check(!state().device.status, "rien n'est enregistre avant « Proposer ce MCF »");
	await page.screenshot({ path: SHOT.replace(".png", "-1-lecture.png"), fullPage: false });

	await page.evaluate((pvf) => cur_dialog.set_value("point_of_sale", pvf), PVF);
	await page.waitForTimeout(400);
	await visibleModal(page).locator(".btn-modal-primary").click();
	await page.waitForFunction(() => /MCF propose/.test(document.body.innerText), null, { timeout: 15000 });
	const s = state();
	check(s.device.status === "Detecte", "MCF propose : etat Detecte, non actif");
	check(s.device.nif === "A0700190Q" && s.device.last_mid === NID + "-1", `NIF et MID enregistres (${s.device.nif}, ${s.device.last_mid})`);
	check(s.device.discovered_by === MANAGER && !s.device.discovered_gateway, "proposition tracee au nom du responsable, sans poste");
	check(s.proposed_for === PVF && s.read_by === "browser-desk", "point de vente prevu et origine de la lecture enregistres");
	check(!s.pvf_mcf && !s.device.point_of_sale, "rien n'est rattache au point de vente (validation requise)");
	check(s.events >= 1, "evenement MCF_PROPOSE journalise");
	const sim = await ask({ op: "state", id: "desk" });
	check(sim.invoices.length === 0 && sim.tc === 0, "aucune facture sur le MCF (lecture sans effet fiscal)");
	await page.screenshot({ path: SHOT.replace(".png", "-2-propose.png"), fullPage: false });
	await ctx.close();

	// 3. a browser without Web Serial (Firefox, Safari, or HTTP): guidance instead of an error
	const ctx2 = await browser.newContext();
	const p2 = await ctx2.newPage();
	await p2.addInitScript(() => { try { delete Navigator.prototype.serial; } catch (e) { /* none */ } });
	await p2.request.post(`${SITE}/api/method/login`, { data: { usr: MANAGER, pwd: PASSWORD } });
	await openSettings(p2);
	await search(p2);
	await p2.waitForFunction(() => /Google Chrome/.test(document.body.innerText), null, { timeout: 15000 });
	check(true, "navigateur sans Web Serial : message Chrome / Edge en HTTPS");
	await ctx2.close();

	await browser.close();
	py.kill();
	console.log("E2E RECHERCHE MCF DEPUIS LES PARAMETRES : TOUT EST OK");
})().catch(async (e) => {
	console.error(e);
	try {
		await global.__page.screenshot({ path: SHOT.replace(".png", "-echec.png"), fullPage: true });
		console.log("[page]", (await global.__page.innerText("body")).slice(0, 1500));
	} catch (x) { /* no page */ }
	py.kill(); process.exit(1);
});
