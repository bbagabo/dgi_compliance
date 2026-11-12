/*
 * v5.4.2 - End-to-end test of "Annuler la demande" and "Rejeter l'approbation" on the Poste MCF page, in a real
 * Chromium (Playwright). Same harness as e2e_revue_541.js (serial port = MCF simulator through sim_bridge.py):
 *   D. the cashier without the role DGI Approbateur does not see the buttons;
 *   E. with the role, she cancels the pending request of the poste (reason asked): back to the enrolment;
 *   F. enrolled and approved again, the poste works; the approval is rejected from the page: the poste stops;
 *   G. a request cancelled by the manager from another computer: the waiting page says so by itself.
 *
 *   node e2e_retrait_542.js <site_url> <enrolment_code>     (code from tests.e2e_station.prepare)
 * BENCH_CMD: command that runs bench in the bench directory (default: su - frappe, as the other e2e tests).
 * TEST SITES ONLY.
 */
"use strict";
const path = require("node:path");
const { spawn, execSync } = require("node:child_process");
const readline = require("node:readline");
const { chromium } = require("playwright");

const [, , SITE, CODE] = process.argv;
const BENCH = process.env.BENCH || "/home/frappe/frappe-bench";
const CASHIER = "caisse.e2e@example.com", PASSWORD = "Caisse#E2E-2026!", GW = "GW-WEB-E2E";

const py = spawn(process.env.PYTHON || "python3", [path.join(__dirname, "sim_bridge.py")], { stdio: ["pipe", "pipe", "inherit"] });
const rl = readline.createInterface({ input: py.stdout });
const waiting = [];
rl.on("line", (l) => waiting.shift()(JSON.parse(l)));
const ask = (req) => new Promise((r) => { waiting.push(r); py.stdin.write(JSON.stringify(req) + "\n"); });

function bench(method, kwargs) {
	const args = ["--site", process.env.SITE_NAME || "test.local", "execute", method];
	if (kwargs) args.push("--kwargs", JSON.stringify(kwargs));
	const quoted = args.map((a) => `'${a}'`).join(" ");
	const cmd = process.env.BENCH_CMD
		? `cd ${BENCH} && ${process.env.BENCH_CMD} ${quoted} 2>/dev/null`
		: `su - frappe -c "[ -f ~/env.sh ] && . ~/env.sh; cd ${BENCH} && bench ${quoted.replace(/"/g, '\\"')} 2>/dev/null"`;
	return execSync(cmd, { shell: "/bin/bash" }).toString().trim().split("\n").pop();
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const check = (cond, msg) => { if (!cond) throw new Error("ECHEC : " + msg); console.log("OK  " + msg); };
const gw = () => JSON.parse(bench("dgi_compliance.tests.e2e_station.gateway_state").replace(/^GW=/, ""));

const FAKE_SERIAL = () => {
	class FakePort {
		getInfo() { return { usbVendorId: 0x1a86, usbProductId: 0x7523 }; }
		async open() {
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
		async close() { try { this.ctrl.close(); } catch (e) { /* closed */ } }
	}
	const port = new FakePort();
	Object.defineProperty(navigator, "serial", { configurable: true, value: {
		requestPort: async () => port, getPorts: async () => [port],
		addEventListener() {}, removeEventListener() {},
	} });
};

async function new_page(ctx) {
	const page = await ctx.newPage();
	page.on("pageerror", (e) => console.log("[page error]", e.message));
	await page.exposeFunction("__mcfWrite", async (hex) => (await ask({ op: "write", id: "web", hex })).hex || "");
	await page.addInitScript(FAKE_SERIAL);
	return page;
}
async function goto_station(page) {
	await page.goto(`${SITE}/app/dgi-mcf-station`);
	await page.waitForSelector(".dgi-st-gw", { timeout: 30000 });
	if ((await page.inputValue(".dgi-st-gw")) !== GW) await page.selectOption(".dgi-st-gw", GW);
}
const step_text = (page) => page.evaluate(() => document.querySelector(".dgi-st-step").innerText);
const alert_text = (page) => page.evaluate(() => document.querySelector(".dgi-st-alert").innerText);
const wait_step = (page, re, timeout) => page.waitForFunction((src) => new RegExp(src).test(document.querySelector(".dgi-st-step").innerText), re.source, { timeout: timeout || 20000 });

async function enrol(page, code) {
	await page.waitForSelector(".dgi-st-code", { timeout: 15000 });
	if (!(await page.locator(".dgi-st-device table").count())) {
		await page.click(".dgi-st-connect");
		await page.waitForSelector(".dgi-st-device table", { timeout: 15000 });
	}
	await page.fill(".dgi-st-code", code);
	if (await page.locator(".modal.show").count()) throw new Error("ECHEC : message inattendu : " + (await page.locator(".modal.show").first().innerText()).replace(/\s+/g, " ").slice(0, 200));
	await page.click(".dgi-st-enrol");
	await wait_step(page, /En attente d'approbation/);
	return (await step_text(page)).match(/[0-9A-F]{4}(-[0-9A-F]{4}){4}/)[0];
}
async function give_reason(page, reason) {
	await page.waitForSelector(".modal.show textarea[data-fieldname='reason']", { timeout: 10000 });
	await page.fill(".modal.show textarea[data-fieldname='reason']", reason);
	await page.click(".modal.show .btn-modal-primary");
}
const new_code = () => bench("dgi_compliance.tests.e2e_station.new_code").replace(/^CODE=/, "");

(async () => {
	await ask({ op: "new", id: "web", nid: "WEB0000001", nif: "A0700190Q", rates: { A: 0, B: 16, C: 5, O: 1, P: 1 } });
	bench("dgi_compliance.tests.e2e_station.grant_approver", { on: 0 });
	const browser = await chromium.launch({ args: [`--unsafely-treat-insecure-origin-as-secure=${SITE}`] });
	const ctx = await browser.newContext();
	const login = await ctx.request.post(`${SITE}/api/method/login`, { data: { usr: CASHIER, pwd: PASSWORD } });
	if (login.status() !== 200) throw new Error("login " + login.status());

	// ---------------------------------------------------------------- D. no role, no button
	let page = await new_page(ctx);
	await goto_station(page);
	const fp1 = await enrol(page, CODE);
	check(gw().status === "A approuver", `D1 poste enrole, en attente d'approbation (empreinte ${fp1})`);
	check(!(await page.locator(".dgi-st-cancel-request").count()), "D2 caissiere sans le role DGI Approbateur : pas de bouton 'Annuler la demande'");
	const refused = await page.evaluate(async (g) => {
		try {
			await frappe.xcall("dgi_compliance.dgi_compliance.doctype.dgi_gateway.dgi_gateway.cancel_enrollment_request", { gateway: g, reason: "sans role" });
			return false;
		} catch (e) { return true; }
	}, GW);
	check(refused && gw().status === "A approuver", "D3 l'appel direct est refuse par le serveur (role requis)");
	for (let i = 0; i < 5 && (await page.locator(".modal.show").count()); i++) {
		await page.locator(".modal.show .btn-modal-close, .modal.show .close").first().click({ timeout: 3000 }).catch(() => page.keyboard.press("Escape"));
		await sleep(700);
	}

	// ---------------------------------------------------------------- E. the approver cancels the pending request
	bench("dgi_compliance.tests.e2e_station.grant_approver", { on: 1 });
	await page.close();
	page = await new_page(ctx);                                   // same profile (same key), roles reloaded
	await goto_station(page);
	await wait_step(page, /En attente d'approbation/);
	check((await step_text(page)).includes(fp1), "E1 meme navigateur : la demande en attente est toujours affichee avec son empreinte");
	await page.waitForSelector(".dgi-st-cancel-request", { timeout: 10000 });
	check(true, "E2 avec le role DGI Approbateur : bouton 'Annuler la demande' affiche");
	await page.click(".dgi-st-cancel-request");
	await give_reason(page, "Mauvais poste : e2e");
	await wait_step(page, /Enrolement de ce navigateur/);
	check(/est annulee/.test(await alert_text(page)), "E3 demande annulee : message sur la page, retour a l'enrolement");
	let g = gw();
	check(g.status === "Nouvelle" && !g.key_fingerprint && g.events.includes("DEMANDE_ANNULEE"), "E4 poste a l'etat Nouvelle, cle effacee, DEMANDE_ANNULEE journalise");

	// ---------------------------------------------------------------- F. approved again, then the approval is rejected
	const fp2 = await enrol(page, new_code());
	check(fp2 !== fp1, `F1 nouvel enrolement avec un nouveau code, nouvelle empreinte ${fp2}`);
	check(bench("dgi_compliance.tests.e2e_station.approve", { fingerprint: fp2 }).includes("APPROVED=Active"), "F2 poste approuve par une autre personne");
	await page.waitForFunction(() => /demarre/.test(document.querySelector(".dgi-st-log").innerText), null, { timeout: 30000 });
	const inv = bench("dgi_compliance.tests.e2e_station.new_invoice").match(/INVOICE=(\S+)/)[1];
	let s = {};
	for (let t = 0; t < 30; t++) {
		s = JSON.parse(bench("dgi_compliance.tests.e2e_station.invoice_state", { name: inv }).replace(/^STATE=/, ""));
		if (s.status === "Normalized") break;
		await sleep(2000);
	}
	check(s.status === "Normalized" && s.channel === "MCF", `F3 poste en service : ${inv} normalisee par le MCF`);
	check(await page.isVisible(".dgi-st-reject"), "F4 poste en marche : bouton 'Rejeter l'approbation' affiche a l'approbateur");
	await page.click(".dgi-st-reject");
	await give_reason(page, "Caisse remplacee : e2e");
	await wait_step(page, /Enrolement de ce navigateur/);
	check(/est rejetee/.test(await alert_text(page)), "F5 approbation rejetee : message sur la page, retour a l'enrolement");
	check(!(await page.evaluate(() => !!cur_page.page.dgi_station.station)) && !(await page.isVisible(".dgi-st-reject")), "F6 le poste est arrete");
	g = gw();
	check(g.status === "Nouvelle" && !g.approved_by && g.events.includes("APPROBATION_REJETEE"), "F7 poste a l'etat Nouvelle, APPROBATION_REJETEE journalise");

	// ---------------------------------------------------------------- G. cancelled by the manager from elsewhere
	const fp3 = await enrol(page, new_code());
	check(gw().status === "A approuver", `G1 nouvelle demande en attente (empreinte ${fp3})`);
	check(bench("dgi_compliance.tests.e2e_station.cancel_request").includes("CANCELLED=Nouvelle"), "G2 le responsable annule la demande depuis son poste");
	await page.waitForFunction(() => /a ete annulee par un responsable/.test(document.querySelector(".dgi-st-alert").innerText), null, { timeout: 30000 });
	await wait_step(page, /Enrolement de ce navigateur/);
	check(true, "G3 la page en attente l'indique seule et repropose l'enrolement");
	const sim = await ask({ op: "state", id: "web" });
	check(sim.invoices.length === 1, "G4 le MCF a enregistre exactement 1 facture");
	await page.screenshot({ path: process.env.SHOT || "/tmp/poste_mcf_542.png", fullPage: true });
	await browser.close();
	py.kill();
	console.log("TOUT EST OK");
})().catch((e) => { console.error(e.message || e); py.kill(); process.exit(1); });
