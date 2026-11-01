"""DGI Gateway - passerelle fiscale de site entre ERPNext (dgi_compliance) et le MCF physique.

Protocole MCF-SFE v1.0 (DGI RDC) sur port serie / COM virtuel USB, 115200 8N1.
Une passerelle pilote UN seul MCF ; elle ne fait que des appels HTTPS sortants vers le site
ERPNext auquel elle est enrolee.
"""
__version__ = "0.2.0"
