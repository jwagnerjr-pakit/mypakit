# -*- coding: utf-8 -*-

from odoo import fields, models, api, _
from odoo.tools import float_is_zero
from collections import defaultdict
from odoo.exceptions import UserError


class BlanketOrderWizard(models.TransientModel):
    _name = 'sale.blanket.order.wizard'
    _description = 'Blanket order wizard'

    blanket_order_id = fields.Many2one(
        'sale.blanket.order', readonly=True)
    sale_order_id = fields.Many2one(
        'sale.order',
        string='Sale Order',
        domain=[('state', '=', 'draft')])
    line_ids = fields.One2many(
        'sale.blanket.order.wizard.line', 'wizard_id',
        string='Lines')

    def create_sale_order(self):
        order_lines_by_customer = defaultdict(list)
        currency_id = 0
        pricelist_id = 0
        user_id = 0
        payment_term_id = 0
        for line in self.line_ids.filtered(lambda l: l.qty != 0.0):
            if line.qty > line.remaining_uom_qty:
                raise UserError(
                    _('You can\'t order more than the remaining quantities'))
            vals = {'product_id': line.product_id.id,
                    'name': line.product_id.name,
                    'product_uom': line.product_uom.id,
                    'sequence': line.blanket_line_id.sequence,
                    'price_unit': line.blanket_line_id.price_unit,
                    'blanket_order_line': line.blanket_line_id.id,
                    'product_uom_qty': line.qty,
                    'tax_id': [(6, 0, line.taxes_id.ids)]}
            order_lines_by_customer[line.partner_id.id].append((0, 0, vals))

            if currency_id == 0:
                currency_id = line.blanket_line_id.order_id.currency_id.id
            elif currency_id != line.blanket_line_id.order_id.currency_id.id:
                currency_id = False

            if pricelist_id == 0:
                pricelist_id = line.blanket_line_id.pricelist_id.id
            elif pricelist_id != line.blanket_line_id.pricelist_id.id:
                pricelist_id = False

            if user_id == 0:
                user_id = line.blanket_line_id.salesman_id.id
            elif user_id != line.blanket_line_id.salesman_id.id:
                user_id = False

            if payment_term_id == 0:
                payment_term_id = line.blanket_line_id.payment_term_id.id
            elif payment_term_id != line.blanket_line_id.payment_term_id.id:
                payment_term_id = False

        if not order_lines_by_customer:
            raise UserError(_('An order can\'t be empty'))

        if not currency_id:
            raise UserError(_('Can not create Sale Order from Blanket '
                              'Order lines with different currencies'))

        res = []
        for customer in order_lines_by_customer:
            order_vals = {
                'partner_id': customer,
                'origin': self.blanket_order_id.name,
                'user_id': user_id,
                'currency_id': currency_id,
                'pricelist_id': pricelist_id,
                'payment_term_id': payment_term_id,
                'order_line': order_lines_by_customer[customer],
            }
            order_obj = sale_order = self.env['sale.order'].create(order_vals)
            for line in order_obj.order_line:
                line._onchange_discount()
            res.append(sale_order.id)
        return {
            'domain': [('id', 'in', res)],
            'name': _('Sales Orders'),
            'view_type': 'form',
            'view_mode': 'tree,form',
            'res_model': 'sale.order',
            'context': {'from_sale_order': True},
            'type': 'ir.actions.act_window'
        }


class BlanketOrderWizardLine(models.TransientModel):
    _name = 'sale.blanket.order.wizard.line'
    _description = 'Blanket order wizard line'

    wizard_id = fields.Many2one('sale.blanket.order.wizard')
    blanket_line_id = fields.Many2one('sale.blanket.order.line')
    product_id = fields.Many2one('product.product', related='blanket_line_id.product_id', string='Product')
    product_uom = fields.Many2one('uom.uom', related='blanket_line_id.product_uom', string='Unit of Measure')
    date_scheduled = fields.Date(related='blanket_line_id.date_scheduled')
    remaining_uom_qty = fields.Float(related='blanket_line_id.remaining_uom_qty')
    qty = fields.Float(string='Quantity to Order', required=True)
    price_unit = fields.Float(related='blanket_line_id.price_unit')
    currency_id = fields.Many2one('res.currency', related='blanket_line_id.currency_id')
    partner_id = fields.Many2one('res.partner', related='blanket_line_id.partner_id', string='Vendor')
    taxes_id = fields.Many2many('account.tax', related="blanket_line_id.tax_id")
