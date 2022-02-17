# -*- coding: utf-8 -*-
{
    'name': "Sales Blanket Order",

    'summary': """Blanket sales order is a long-term agreement between a seller and a customer""",

    'description': """
         A blanket sales order is a long-term agreement between a seller and a customer. A
                                blanket order is typically made when a customer has committed to purchasing large
                                quantities that are to be delivered in several smaller shipments over a certain period
                                of time.
    """,

    'author': 'ErpMstar Solutions',
    'category': 'Sales',
    'version': '1.0',

    # any module necessary for this one to work correctly
    'depends': ['sale_management'],

    # always loaded
    'data': [
        'wizard/create_sale_orders.xml',
        'security/ir.model.access.csv',
        'views/views.xml',
        'data/sequence.xml',
        'data/cron.xml',
    ],
    'images': ['static/description/blanket_banner.png'],
    'price': 35,
    'currency': 'EUR',
    'installable': True,
}
