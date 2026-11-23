"""French and Portuguese: the languages of Central and Lusophone Southern Africa.

Same structure as the tables in app.core.languages, which merges these in at import.

French   Democratic Republic of the Congo, Republic of the Congo, Gabon, Cameroon, Chad,
         Central African Republic (Cameroon is officially bilingual: set it to "en" in the
         commercial settings' `languages` if your buyers there prefer English).
Portuguese  Angola, Mozambique.

Translation is done by the AI model and verified by a second pass (back-translation checked
against the same claim rules as English), exactly as for Arabic, Turkish and Hebrew; it is
not a native speaker. Have someone who reads the language look over one page and a few
e-mails before relying on a new market. Portuguese wording follows standard European usage,
which is what business correspondence in Angola and Mozambique uses.
"""

from __future__ import annotations

from typing import Any

LANGUAGES: dict[str, dict[str, Any]] = {
    "fr": {"name": "French", "native": "Français", "rtl": False, "google_language": 1002},
    "pt": {"name": "Portuguese", "native": "Português", "rtl": False, "google_language": 1014},
}

COUNTRY_LANGUAGES = {
    "Democratic Republic of the Congo": "fr", "Republic of the Congo": "fr", "Gabon": "fr", "Cameroon": "fr",
    "Chad": "fr", "Central African Republic": "fr",
    "Angola": "pt", "Mozambique": "pt", "Sao Tome and Principe": "pt",
}

COUNTRY_NAMES = {
    "fr": {"Democratic Republic of the Congo": "République démocratique du Congo",
           "Republic of the Congo": "République du Congo", "Cameroon": "Cameroun", "Gabon": "Gabon",
           "Chad": "Tchad", "Central African Republic": "République centrafricaine",
           "Equatorial Guinea": "Guinée équatoriale", "Burundi": "Burundi", "Rwanda": "Rwanda",
           "Madagascar": "Madagascar", "Mauritius": "Maurice", "Comoros": "Comores", "Djibouti": "Djibouti"},
    "pt": {"Angola": "Angola", "Mozambique": "Moçambique", "Sao Tome and Principe": "São Tomé e Príncipe"},
}

# Stock may be new or used/refurbished, so names never say which.
CATEGORY_NAMES = {
    "fr": {"medical_equipment": "matériel médical", "pharmaceutical": "produits pharmaceutiques",
           "refurbished_laptop": "ordinateurs portables", "used_iphone": "iPhone",
           "server_it": "serveurs et équipements informatiques"},
    "pt": {"medical_equipment": "equipamento médico", "pharmaceutical": "produtos farmacêuticos",
           "refurbished_laptop": "computadores portáteis", "used_iphone": "iPhone",
           "server_it": "servidores e equipamento informático"},
}

STRINGS: dict[str, dict[str, str]] = {
    "fr": {
        "wa_header": "Écrivez-nous sur WhatsApp", "wa_chat": "Discuter sur WhatsApp",
        "wa_continue": "Continuer sur WhatsApp", "privacy": "Politique de confidentialité",
        "serving": "Nous desservons : {country}", "reply": "Réponse {promise}", "promise": "sous un jour ouvré",
        "what_we_supply": "Ce que nous fournissons", "why_us": "Pourquoi les acheteurs travaillent avec nous",
        "how_it_works": "Comment cela fonctionne", "step1_t": "Dites-nous ce dont vous avez besoin",
        "step1_d": "Modèles, quantité et lieu de livraison.", "step2_t": "Recevez un devis écrit",
        "step2_d": "Prix, état, garantie, délai et conditions, {promise}.",
        "step3_t": "Confirmez et recevez", "step3_d": "Vous ne vous engagez qu'à l'acceptation du devis.",
        "request_quote": "Demander un devis", "name": "Votre nom", "organisation": "Organisation",
        "email": "E-mail professionnel", "phone": "Téléphone / WhatsApp", "product": "Produit",
        "any_product": "Au choix / je ne sais pas", "quantity": "Quantité", "need": "De quoi avez-vous besoin ?",
        "need_ph": "Spécifications, lieu de livraison, délais",
        "consent": "J'accepte d'être contacté(e) au sujet de cette demande. Voir la", "questions": "Questions",
        "about": "À propos de nous",
        "price": "Prix indicatifs à partir de <b>{price} USD</b> l'unité &mdash; votre devis dépend de la quantité, des spécifications et de la livraison.",
        "warranty": "garantie", "moq": "commande minimale",
        "thanks_title": "Merci &mdash; nous avons bien reçu votre demande",
        "thanks_body": "Nous vous répondrons par e-mail {promise} avec un devis écrit ou nos questions.",
        "wa_text": "Bonjour, je souhaite recevoir un devis pour {topic}.", "card_cta": "Demander un devis",
        "ack_subject": "Nous avons bien reçu votre demande",
        "ack": "Bonjour {name},\n\nMerci pour votre demande concernant {topic}{qty}. Nous l'avons bien reçue et vous répondrons {promise} avec un devis écrit ou nos éventuelles questions.\n\nVous pouvez répondre à cet e-mail en précisant les spécifications exactes, le lieu de livraison et le calendrier souhaité.\n\nCordialement,\n{sender}",
        "units": "unités",
        "optout": "Pour ne plus recevoir ces e-mails, répondez « désabonnement »", "optout_link": " ou utilisez {link}",
        "err_many": "Trop de demandes ; veuillez réessayer plus tard",
        "err_name": "Veuillez indiquer votre nom et votre organisation",
        "err_email": "Veuillez saisir une adresse e-mail valide",
        "err_consent": "Veuillez accepter d'être contacté(e) au sujet de cette demande",
        "err_qty": "La quantité doit être un nombre", "err_qty_range": "La quantité doit être comprise entre 1 et 1 000 000",
    },
    "pt": {
        "wa_header": "Escreva-nos no WhatsApp", "wa_chat": "Fale connosco no WhatsApp",
        "wa_continue": "Continuar no WhatsApp", "privacy": "Aviso de privacidade",
        "serving": "A servir {country}", "reply": "Resposta {promise}", "promise": "num dia útil",
        "what_we_supply": "O que fornecemos", "why_us": "Porque os compradores trabalham connosco",
        "how_it_works": "Como funciona", "step1_t": "Diga-nos o que precisa",
        "step1_d": "Modelos, quantidade e local de entrega.", "step2_t": "Receba uma cotação por escrito",
        "step2_d": "Preço, estado, garantia, prazo e condições, {promise}.",
        "step3_t": "Confirme e receba", "step3_d": "Só assume um compromisso quando aceitar a cotação.",
        "request_quote": "Pedir cotação", "name": "O seu nome", "organisation": "Organização",
        "email": "E-mail profissional", "phone": "Telefone / WhatsApp", "product": "Produto",
        "any_product": "Qualquer / não tenho a certeza", "quantity": "Quantidade", "need": "De que precisa?",
        "need_ph": "Especificações, local de entrega, prazo",
        "consent": "Aceito ser contactado(a) sobre este pedido. Consulte o", "questions": "Perguntas",
        "about": "Sobre nós",
        "price": "Preços indicativos a partir de <b>{price} USD</b> por unidade &mdash; a cotação depende da quantidade, das especificações e da entrega.",
        "warranty": "garantia", "moq": "encomenda mínima", "thanks_title": "Obrigado &mdash; recebemos o seu pedido",
        "thanks_body": "Responderemos por e-mail {promise} com uma cotação por escrito ou eventuais questões.",
        "wa_text": "Olá, gostaria de receber uma cotação para {topic}.", "card_cta": "Pedir cotação",
        "ack_subject": "Recebemos o seu pedido",
        "ack": "Olá {name},\n\nObrigado pelo seu pedido sobre {topic}{qty}. Recebemo-lo e responderemos {promise} com uma cotação por escrito ou eventuais questões.\n\nSe possível, responda a este e-mail com as especificações exatas, o local de entrega e o prazo pretendido.\n\nCom os melhores cumprimentos,\n{sender}",
        "units": "unidades",
        "optout": "Para deixar de receber estes e-mails, responda «cancelar subscrição»", "optout_link": " ou utilize {link}",
        "err_many": "Demasiados pedidos; tente novamente mais tarde",
        "err_name": "Indique o seu nome e a organização",
        "err_email": "Indique um endereço de e-mail válido",
        "err_consent": "Aceite ser contactado(a) sobre este pedido",
        "err_qty": "A quantidade deve ser um número", "err_qty_range": "A quantidade deve estar entre 1 e 1 000 000",
    },
}

OPT_OUT_PHRASES = {
    "fr": ["désabonnement", "me désabonner", "désinscription", "me désinscrire", "retirez-moi", "supprimez-moi",
           "ne m'envoyez plus", "cessez de m'envoyer", "arrêtez de m'envoyer", "ne plus recevoir"],
    "pt": ["cancelar subscrição", "cancelar a subscrição", "cancelar inscrição", "anular subscrição",
           "remova-me", "removam-me", "remover da lista", "não envie mais", "deixem de enviar", "parem de enviar",
           "descadastrar"],
}

# Same meaning as the English lists in app/policies/fact_check.py and app/ads/compliance.py.
# Phrases avoid words that are also ordinary (French "garantie" = warranty, Portuguese "procura").
CLAIMS = {
    "fr": {
        "medical": ["guérit", "traite toutes les maladies", "sans effets secondaires", "cliniquement prouvé",
                    "sans danger pour tous les patients"],
        "regulatory": ["approuvé par la fda", "approuvé fda", "agréé fda", "marquage ce", "certifié ce",
                       "approuvé par l'oms", "préqualifié oms", "certifié iso", "certifié bpf", "certifié gmp"],
        "relationship": ["distributeur agréé", "distributeur autorisé", "distributeur officiel", "revendeur agréé",
                         "revendeur officiel", "distributeur exclusif", "agent exclusif", "partenaire exclusif",
                         "représentant officiel"],
        "license": ["importateur agréé", "importateur autorisé", "importateur licencié", "licence d'importation",
                    "autorisation d'importation", "importateur enregistré", "titulaire d'une licence"],
        "ad": ["résultat garanti", "satisfaction garantie", "100 % garanti", "prix garanti", "meilleur prix",
               "prix le plus bas", "le moins cher", "numéro 1", "numéro un", "livraison gratuite", "sans risque",
               "imbattable", "offre limitée"],
        "new": ["tout neuf", "toute neuve", "flambant neuf", "neuf et scellé", "scellé d'usine",
                "sous emballage d'origine"],
    },
    "pt": {
        "medical": ["cura todas as doenças", "cura qualquer doença", "trata todas as doenças",
                    "sem efeitos secundários", "sem efeitos colaterais", "clinicamente comprovado",
                    "seguro para todos os pacientes"],
        "regulatory": ["aprovado pela fda", "aprovado fda", "marcação ce", "marcado ce", "certificado ce",
                       "aprovado pela oms", "pré-qualificado pela oms", "certificado iso", "certificado gmp"],
        "relationship": ["distribuidor autorizado", "distribuidor oficial", "revendedor autorizado",
                         "revendedor oficial", "distribuidor exclusivo", "representante exclusivo",
                         "parceiro exclusivo", "representante oficial", "agente autorizado"],
        "license": ["importador licenciado", "importador autorizado", "importador registado",
                    "importador registrado", "licença de importação", "titular de licença"],
        "ad": ["garantido", "resultados garantidos", "melhor preço", "preço mais baixo", "o mais barato",
               "número 1", "número um", "envio gratuito", "portes grátis", "frete grátis", "sem risco", "imbatível"],
        "new": ["novo em folha", "novinho em folha", "totalmente novo", "totalmente nova", "selado de fábrica",
                "lacrado de fábrica", "embalagem original selada"],
    },
}

# Local-language buyer research (tenders, hospitals, wholesalers). {country} is the local country name.
PROSPECT_QUERIES = {
    "fr": {
        "medical_equipment": ["appel d'offres équipement médical {country}", "hôpital achat matériel médical {country}"],
        "pharmaceutical": ["appel d'offres médicaments {country}", "grossiste répartiteur pharmaceutique {country}"],
        "refurbished_laptop": ["appel d'offres ordinateurs portables {country}", "université ONG achat ordinateurs {country}"],
        "used_iphone": ["grossiste téléphones mobiles {country}", "revendeur smartphones iPhone {country}"],
        "server_it": ["appel d'offres serveurs informatiques {country}", "opérateur data center {country}"],
    },
    "pt": {
        "medical_equipment": ["concurso público equipamento médico {country}", "hospital aquisição equipamento médico {country}"],
        "pharmaceutical": ["concurso fornecimento medicamentos {country}", "distribuidor grossista farmacêutico {country}"],
        "refurbished_laptop": ["concurso fornecimento computadores portáteis {country}", "universidade ONG aquisição computadores {country}"],
        "used_iphone": ["grossista telemóveis {country}", "revendedor smartphones iPhone {country}"],
        "server_it": ["concurso servidores informáticos {country}", "operador data center {country}"],
    },
}
PLACE_QUERIES = {
    "fr": {
        "medical_equipment": ["hôpital {country}", "centre de diagnostic {country}"],
        "pharmaceutical": ["grossiste pharmaceutique {country}", "pharmacie {country}"],
        "refurbished_laptop": ["université {country}", "école internationale {country}"],
        "used_iphone": ["grossiste téléphones {country}", "boutique de téléphones {country}"],
        "server_it": ["data center {country}", "siège de banque {country}"],
    },
    "pt": {
        "medical_equipment": ["hospital {country}", "centro de diagnóstico {country}"],
        "pharmaceutical": ["grossista farmacêutico {country}", "farmácia {country}"],
        "refurbished_laptop": ["universidade {country}", "escola internacional {country}"],
        "used_iphone": ["grossista telemóveis {country}", "loja de telemóveis {country}"],
        "server_it": ["data center {country}", "sede de banco {country}"],
    },
}

# Google search-ad keywords (phrase match). Neutral about condition: they work for new and used stock.
# Pharmaceuticals are never advertised.
AD_KEYWORDS = {
    "fr": {
        "medical_equipment": [
            "fournisseur matériel médical", "équipement médical en gros", "fournisseur équipement hospitalier",
            "moniteur patient fournisseur", "échographe fournisseur", "électrocardiographe fournisseur",
            "équipement de laboratoire médical", "matériel médical {country}",
            "fournisseur équipement médical {country}", "distributeur matériel médical {country}",
        ],
        "refurbished_laptop": [
            "fournisseur ordinateurs portables", "ordinateurs portables en gros", "grossiste ordinateurs portables",
            "ordinateurs portables professionnels", "ordinateurs portables pour écoles", "ordinateurs portables pour ong",
            "fournisseur ordinateurs portables {country}", "ordinateurs portables en gros {country}",
        ],
        "used_iphone": [
            "grossiste iphone", "fournisseur iphone en gros", "iphone en gros", "iphone en gros {country}",
            "grossiste iphone {country}", "fournisseur iphone {country}",
        ],
        "server_it": [
            "fournisseur serveurs", "serveurs dell poweredge", "serveurs hpe proliant", "fournisseur équipement réseau",
            "équipement data center fournisseur", "serveurs informatiques {country}", "fournisseur serveurs {country}",
        ],
    },
    "pt": {
        "medical_equipment": [
            "fornecedor de equipamento médico", "equipamento médico por grosso", "fornecedor de material hospitalar",
            "monitor de pacientes fornecedor", "ecógrafo fornecedor", "eletrocardiógrafo fornecedor",
            "equipamento de laboratório médico", "equipamento médico {country}",
            "fornecedor de equipamento médico {country}",
        ],
        "refurbished_laptop": [
            "fornecedor de portáteis", "portáteis por grosso", "computadores portáteis empresariais",
            "portáteis para escolas", "portáteis para ong", "portáteis {country}", "fornecedor de portáteis {country}",
        ],
        "used_iphone": [
            "iphone por grosso", "fornecedor de iphone", "iphone grossista", "iphone por grosso {country}",
            "fornecedor de iphone {country}",
        ],
        "server_it": [
            "fornecedor de servidores", "servidores dell poweredge", "servidores hpe proliant",
            "fornecedor de equipamento de rede", "equipamento para data center", "servidores {country}",
            "fornecedor de servidores {country}",
        ],
    },
}
AD_NEGATIVES = {
    "fr": ["emploi", "emplois", "recrutement", "salaire", "gratuit", "gratuitement", "réparation", "dépannage",
           "formation", "cours", "télécharger", "location", "symptômes", "traitement", "stage"],
    "pt": ["emprego", "empregos", "recrutamento", "salário", "grátis", "gratuito", "reparação", "conserto",
           "formação", "curso", "descarregar", "baixar", "aluguer", "sintomas", "tratamento", "estágio"],
}
