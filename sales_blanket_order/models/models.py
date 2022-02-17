# -*- coding: utf-8 -*-
import datetime

from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_is_zero, format_date


class BlanketOrder(models.Model):
    _name = "sale.blanket.order"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _description = "Sales Blanket Order"

    @api.model
    def get_default_sales_team(self):
        return self.env['crm.team']._get_default_team_id()

    name = fields.Char(string='Order Reference', required=True, copy=False, readonly=True,
                       states={'draft': [('readonly', False)]}, index=True, default=lambda self: _('New'))
    state = fields.Selection([('draft', 'Draft'), ('open', 'Open'), ('done', 'Done'), ('expired', 'Expired')],
                             string='State', readonly=True, copy=False, index=True, tracking=3, default='draft',
                             compute='compute_order_state', store=True)
    date_order = fields.Datetime(string='Blanket Order Date', required=True, readonly=True, index=True,
                                 states={'draft': [('readonly', False)], 'sent': [('readonly', False)]}, copy=False,
                                 default=fields.Datetime.now,
                                 help="Creation date of draft orders.")
    validity_date = fields.Date(string='Expiration Date', readonly=True, copy=False,
                                states={'draft': [('readonly', False)]})
    confirmed = fields.Boolean(copy=False)

    user_id = fields.Many2one('res.users', string='Salesperson', index=True, tracking=2,
                              default=lambda self: self.env.user, domain=lambda self: [
            ('groups_id', 'in', self.env.ref('sales_team.group_sale_salesman').id)])
    partner_id = fields.Many2one('res.partner', string='Customer', readonly=True,
                                 states={'draft': [('readonly', False)]}, required=True, change_default=True,
                                 index=True, tracking=1,
                                 domain="['|', ('company_id', '=', False), ('company_id', '=', company_id)]", )
    partner_invoice_id = fields.Many2one('res.partner', string='Invoice Address', readonly=True, required=True,
                                         states={'draft': [('readonly', False)], 'sent': [('readonly', False)],
                                                 'sale': [('readonly', False)]},
                                         domain="['|', ('company_id', '=', False), ('company_id', '=', company_id)]", )
    partner_shipping_id = fields.Many2one('res.partner', string='Delivery Address', readonly=True, required=True,
                                          states={'draft': [('readonly', False)], 'sent': [('readonly', False)],
                                                  'sale': [('readonly', False)]},
                                          domain="['|', ('company_id', '=', False), ('company_id', '=', company_id)]", )

    pricelist_id = fields.Many2one('product.pricelist', string='Pricelist', check_company=True, required=True,
                                   readonly=True, states={'draft': [('readonly', False)]},
                                   domain="['|', ('company_id', '=', False), ('company_id', '=', company_id)]",
                                   help="If you change the pricelist, only newly added lines will be affected.")
    currency_id = fields.Many2one("res.currency", related='pricelist_id.currency_id', string="Currency", readonly=True,
                                  required=True)

    order_line = fields.One2many('sale.blanket.order.line', 'order_id', string='Order Lines',
                                 states={'open': [('readonly', True)], 'done': [('readonly', True)],
                                         'expired': [('readonly', True)]}, copy=True)
    order_line_count = fields.Integer('Sale Blanket Order Line count', compute='compute_order_line_count',
                                      readonly=True)
    sale_order_count = fields.Integer(compute='compute_sale_order_count')

    note = fields.Text('Terms and conditions')
    amount_untaxed = fields.Monetary(string='Untaxed Amount', store=True, readonly=True, compute='compute_amount_all',
                                     tracking=5)
    amount_tax = fields.Monetary(string='Taxes', store=True, readonly=True, compute='compute_amount_all')
    amount_total = fields.Monetary(string='Total', store=True, readonly=True, compute='compute_amount_all', tracking=4)
    payment_term_id = fields.Many2one('account.payment.term', string='Payment Terms', check_company=True,
                                      domain="['|', ('company_id', '=', False), ('company_id', '=', company_id)]", )
    fiscal_position_id = fields.Many2one('account.fiscal.position', string='Fiscal Position',
                                         domain="[('company_id', '=', company_id)]", check_company=True,
                                         help="Fiscal positions are used to adapt taxes and accounts for particular "
                                              "customers or sales orders/invoices. The default value comes from the "
                                              "customer.")

    company_id = fields.Many2one('res.company', 'Company', required=True, index=True,
                                 default=lambda self: self.env.company)
    team_id = fields.Many2one('crm.team', 'Sales Team', change_default=True, default=get_default_sales_team,
                              check_company=True,
                              domain="['|', ('company_id', '=', False), ('company_id', '=', company_id)]")

    def get_sale_orders(self):
        return self.mapped('order_line.sale_order_lines.order_id')

    @api.depends('order_line')
    def compute_order_line_count(self):
        self.order_line_count = len(self.mapped('order_line'))

    def compute_sale_order_count(self):
        for blanket_order in self:
            blanket_order.sale_order_count = len(blanket_order.get_sale_orders())

    @api.depends('order_line.remaining_uom_qty', 'validity_date', 'confirmed')
    def compute_order_state(self):
        today = fields.Date.today()
        precision = self.env['decimal.precision'].precision_get('Product Unit of Measure')
        for order in self:
            if not order.confirmed:
                order.state = 'draft'
            elif order.validity_date <= today:
                order.state = 'expired'
            elif float_is_zero(sum(order.order_line.mapped('remaining_uom_qty')), precision_digits=precision):
                if order.order_line:
                    order.state = 'done'
                else:
                    order.state = 'draft'

            else:
                order.state = 'open'

    @api.model
    def expire_the_orders(self):
        today = fields.Date.today()
        expired_orders = self.search([
            ('state', '=', 'open'),
            ('validity_date', '<=', today),
        ])
        expired_orders.modified(['validity_date'])
        expired_orders.recompute()

    def validate_fields(self):
        try:
            today = fields.Date.today()
            for order in self:
                assert order.validity_date, _("Expiration Date Required")
                assert order.validity_date > today, _("Expiration Date must be in the future")
                assert len(order.order_line) > 0, _("Order must have some lines")
                order.order_line.validate_fields()
        except AssertionError as e:
            raise UserError(e)

    def set_to_draft(self):
        for order in self:
            order.write({'state': 'draft'})
        return True

    def action_confirm(self):
        self.validate_fields()
        for order in self:
            sequence_obj = self.env['ir.sequence']
            if order.company_id:
                sequence_obj = sequence_obj.with_context(
                    force_company=order.company_id.id)
            name = sequence_obj.next_by_code('sale.blanket.order')
            order.write({'confirmed': True, 'name': name})
        return True

    def action_cancel(self):
        for order in self:
            if order.sale_order_count > 0:
                for so in order.get_sale_orders():
                    if so.state not in 'cancel':
                        raise UserError(_(
                            'You can not delete/cancel a blanket order with opened '
                            'sales orders! '
                            'Try to cancel them before.'))
            order.write({'state': 'expired'})
        return True

    @api.depends('order_line.price_total')
    def compute_amount_all(self):
        """
        Compute the total amounts of the SO.
        """
        for order in self:
            amount_untaxed = amount_tax = 0.0
            for line in order.order_line:
                amount_untaxed += line.price_subtotal
                amount_tax += line.price_tax
            order.update({
                'amount_untaxed': amount_untaxed,
                'amount_tax': amount_tax,
                'amount_total': amount_untaxed + amount_tax,
            })

    @api.onchange('partner_shipping_id', 'partner_id')
    def onchange_partner_shipping_id(self):
        """
        Trigger the change of fiscal position when the shipping address is modified.
        """
        self.fiscal_position_id = self.env['account.fiscal.position'].with_context(
            force_company=self.company_id.id).get_fiscal_position(
            self.partner_id.id,
            self.partner_shipping_id.id)
        return {}

    @api.onchange('user_id')
    def onchange_user_id(self):
        if self.user_id and self.user_id.sale_team_id:
            self.team_id = self.user_id.sale_team_id

    @api.onchange('partner_id')
    def onchange_partner_id(self):
        """
        Update the following fields when the partner is changed:
        - Pricelist
        - Payment terms
        - Invoice address
        - Delivery address
        """
        if not self.partner_id:
            self.update({
                'partner_invoice_id': False,
                'partner_shipping_id': False,
                'payment_term_id': False,
                'fiscal_position_id': False,
            })
            return

        addr = self.partner_id.address_get(['delivery', 'invoice'])
        partner_user = self.partner_id.user_id or self.partner_id.commercial_partner_id.user_id
        values = {
            'pricelist_id': self.partner_id.property_product_pricelist and self.partner_id.property_product_pricelist.id or False,
            'payment_term_id': self.partner_id.property_payment_term_id and self.partner_id.property_payment_term_id.id or False,
            'partner_invoice_id': addr['invoice'], 'partner_shipping_id': addr['delivery'],
            'user_id': partner_user.id or self.env.uid,
            'team_id': partner_user.team_id.id if partner_user and partner_user.team_id else self.team_id}
        self.update(values)

    def view_sale_orders(self):
        sale_orders = self.get_sale_orders()
        action = self.env.ref('sale.action_orders').read()[0]
        if len(sale_orders) > 0:
            action['domain'] = [('id', 'in', sale_orders.ids)]
            action['context'] = [('id', 'in', sale_orders.ids)]
        else:
            action = {'type': 'ir.actions.act_window_close'}
        return action

    def view_sale_blanket_order_lines(self):
        action = self.env.ref('sales_blanket_order.action_sale_blanket_order_line').read()[0]
        lines = self.mapped('order_line')
        if len(lines) > 0:
            action['domain'] = [('id', 'in', lines.ids)]
        return action

    def _check_valid_blanket_order_line(self, bo_lines):
        precision = self.env['decimal.precision'].precision_get(
            'Product Unit of Measure')
        company_id = False

        if all(float_is_zero(
                line.remaining_uom_qty, precision_digits=precision)
               for line in bo_lines):
            raise UserError(
                _('The sale has already been completed.'))

        for line in bo_lines:
            if line.order_id.state != 'open':
                raise UserError(
                    _('Sale Blanket Order %s is not open') %
                    line.order_id.name)
            line_company_id = line.company_id and line.company_id.id or False
            if company_id is not False \
                    and line_company_id != company_id:
                raise UserError(
                    _('You have to select lines '
                      'from the same company.'))
            else:
                company_id = line_company_id

    def _default_order(self):
        self.expire_the_orders()
        if self.state == 'expired':
            raise UserError(_('You can\'t create a sale order from '
                              'an expired blanket order!'))
        return self

    def create_sale_order_wizard(self):
        bo_lines = self._default_order().order_line
        self._check_valid_blanket_order_line(bo_lines)

        lines = [(0, 0, {
            'blanket_line_id': l.id,
            'product_id': l.product_id.id,
            'date_scheduled': l.date_scheduled,
            'remaining_uom_qty': l.remaining_uom_qty,
            'price_unit': l.price_unit,
            'product_uom': l.product_uom,
            'qty': l.remaining_uom_qty,
            'partner_id': l.partner_id,
        }) for l in bo_lines.filtered(lambda l: l.remaining_uom_qty != 0.0)]

        res_id = (
            self.env['sale.blanket.order.wizard'].create({'line_ids': lines})).id
        return {
            'name': 'Create Sale Order',
            'type': 'ir.actions.act_window',
            'res_model': 'sale.blanket.order.wizard',
            'view_type': 'form',
            'view_mode': 'form',
            'view_id': False,
            'res_id': res_id,
            'target': 'new',
        }


class BlanketOrderLine(models.Model):
    _name = 'sale.blanket.order.line'
    _description = 'Blanket Order Line'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    @api.depends('product_uom_qty', 'discount', 'price_unit', 'tax_id')
    def _compute_amount(self):
        """
        Compute the amounts of the SO line.
        """
        for line in self:
            price = line.price_unit * (1 - (line.discount or 0.0) / 100.0)
            taxes = line.tax_id.compute_all(price, line.order_id.currency_id, line.product_uom_qty,
                                            product=line.product_id, partner=line.order_id.partner_shipping_id)
            line.update({
                'price_tax': sum(t.get('amount', 0.0) for t in taxes.get('taxes', [])),
                'price_total': taxes['total_included'],
                'price_subtotal': taxes['total_excluded'],
            })

    order_id = fields.Many2one('sale.blanket.order', string='Order Reference', required=True, ondelete='cascade',
                               index=True, copy=False)
    name = fields.Text(string='Description', required=True)
    sequence = fields.Integer(string='Sequence')

    price_unit = fields.Float('Unit Price', required=True, digits='Product Price', default=0.0)
    date_scheduled = fields.Date(string='Scheduled Date')
    sale_order_lines = fields.One2many('sale.order.line', 'blanket_order_line', string='Sale order lines',
                                       readonly=True,
                                       copy=False)
    price_subtotal = fields.Monetary(compute='_compute_amount', string='Subtotal', readonly=True, store=True)
    price_tax = fields.Float(compute='_compute_amount', string='Total Tax', readonly=True, store=True)
    price_total = fields.Monetary(compute='_compute_amount', string='Total', readonly=True, store=True)
    tax_id = fields.Many2many('account.tax', string='Taxes',
                              domain=['|', ('active', '=', False), ('active', '=', True)])
    discount = fields.Float(string='Discount (%)', digits='Discount', default=0.0)

    product_id = fields.Many2one('product.product', string='Product',
                                 domain="[('sale_ok', '=', True), '|', ('company_id', '=', False), ('company_id', "
                                        "'=', company_id)]",
                                 change_default=True, ondelete='restrict', check_company=True)
    product_template_id = fields.Many2one('product.template', string='Product Template',
                                          related="product_id.product_tmpl_id", domain=[('sale_ok', '=', True)])

    product_uom_qty = fields.Float(string='Quantity', digits='Product Unit of Measure', required=True, default=1.0)
    product_uom = fields.Many2one('uom.uom', string='Unit of Measure',
                                  domain="[('category_id', '=', product_uom_category_id)]")
    product_uom_category_id = fields.Many2one(related='product_id.uom_id.category_id', readonly=True)

    product_custom_attribute_value_ids = fields.One2many('product.attribute.custom.value', 'sale_order_line_id',
                                                         string="Custom Values")
    product_no_variant_attribute_value_ids = fields.Many2many('product.template.attribute.value', string="Extra Values",
                                                              ondelete='restrict')
    pricelist_id = fields.Many2one(
        related='order_id.pricelist_id', string='Pricelist')

    ordered_uom_qty = fields.Float(string='Ordered quantity', compute='compute_quantities', store=True)
    invoiced_uom_qty = fields.Float(string='Invoiced quantity', compute='compute_quantities', store=True)
    remaining_uom_qty = fields.Float(string='Remaining quantity', compute='compute_quantities', store=True)
    remaining_qty = fields.Float(string='Remaining quantity in base UoM', compute='compute_quantities', store=True)
    delivered_uom_qty = fields.Float(string='Delivered quantity', compute='compute_quantities', store=True)

    salesman_id = fields.Many2one(related='order_id.user_id', store=True, string='Salesperson', readonly=True)
    payment_term_id = fields.Many2one(
        related='order_id.payment_term_id', string='Payment Terms')
    currency_id = fields.Many2one(related='order_id.currency_id', depends=['order_id'], store=True, string='Currency',
                                  readonly=True)
    company_id = fields.Many2one(related='order_id.company_id', string='Company', store=True, readonly=True, index=True)
    partner_id = fields.Many2one(related='order_id.partner_id', store=True, string='Customer', readonly=False)

    def name_get(self):
        result = []
        if self.env.context.get('from_sale_order'):
            for record in self:
                res = "[%s]" % record.order_id.name
                if record.date_scheduled:
                    formatted_date = format_date(record.env, record.date_scheduled)
                    res += ' - %s: %s' % (_('Date Scheduled'), formatted_date)
                res += ' (%s: %s %s)' % (_('remaining'), record.remaining_uom_qty, record.product_uom.name)
                result.append((record.id, res))
            return result
        return super().name_get()

    @api.depends('sale_order_lines.order_id.state', 'sale_order_lines.blanket_order_line',
                 'sale_order_lines.product_uom_qty', 'sale_order_lines.product_uom', 'sale_order_lines.qty_delivered',
                 'sale_order_lines.qty_invoiced', 'product_uom_qty', 'product_uom', )
    def compute_quantities(self):
        for line in self:
            sale_order_lines = line.sale_order_lines
            line.ordered_uom_qty = sum(
                lin.product_uom._compute_quantity(lin.product_uom_qty, line.product_uom) for lin in sale_order_lines if
                lin.order_id.state != 'cancel' and lin.product_id == line.product_id)
            line.invoiced_uom_qty = sum(
                lin.product_uom._compute_quantity(lin.qty_invoiced, line.product_uom) for lin in sale_order_lines if
                lin.order_id.state != 'cancel' and lin.product_id == line.product_id)
            line.delivered_uom_qty = sum(
                lin.product_uom._compute_quantity(lin.qty_delivered, line.product_uom) for lin in sale_order_lines if
                lin.order_id.state != 'cancel' and lin.product_id == line.product_id)
            line.remaining_uom_qty = line.product_uom_qty - line.ordered_uom_qty
            line.remaining_qty = line.product_uom._compute_quantity(line.remaining_uom_qty, line.product_id.uom_id)

    @api.onchange('product_id')
    def product_id_change(self):
        if not self.product_id:
            return
        valid_values = \
            self.product_id.product_tmpl_id.valid_product_template_attribute_line_ids.product_template_value_ids
        # remove the is_custom values that don't belong to this template
        for pacv in self.product_custom_attribute_value_ids:
            if pacv.custom_product_template_attribute_value_id not in valid_values:
                self.product_custom_attribute_value_ids -= pacv

        # remove the no_variant attributes that don't belong to this template
        for ptav in self.product_no_variant_attribute_value_ids:
            if ptav._origin not in valid_values:
                self.product_no_variant_attribute_value_ids -= ptav

        vals = {}
        if not self.product_uom or (self.product_id.uom_id.id != self.product_uom.id):
            vals['product_uom'] = self.product_id.uom_id
            vals['product_uom_qty'] = self.product_uom_qty or 1.0

        product = self.product_id.with_context(
            lang=self.order_id.partner_id.lang,
            partner=self.order_id.partner_id,
            quantity=vals.get('product_uom_qty') or self.product_uom_qty,
            date=self.order_id.date_order,
            pricelist=self.order_id.pricelist_id.id,
            uom=self.product_uom.id
        )

        vals.update(name=self.get_sale_order_line_multiline_description_sale(product))

        self._compute_tax_id()

        if self.order_id.pricelist_id and self.order_id.partner_id:
            vals['price_unit'] = self.env['account.tax']._fix_tax_included_price_company(
                self._get_display_price(product), product.taxes_id, self.tax_id, self.company_id)
        self.update(vals)

        title = False
        message = False
        result = {}
        warning = {}
        if product.sale_line_warn != 'no-message':
            title = _("Warning for %s") % product.name
            message = product.sale_line_warn_msg
            warning['title'] = title
            warning['message'] = message
            result = {'warning': warning}
            if product.sale_line_warn == 'block':
                self.product_id = False

        return result

    def _get_display_price(self, product):
        no_variant_attributes_price_extra = [
            ptav.price_extra for ptav in self.product_no_variant_attribute_value_ids.filtered(
                lambda ptav:
                ptav.price_extra and
                ptav not in product.product_template_attribute_value_ids
            )
        ]
        if no_variant_attributes_price_extra:
            product = product.with_context(
                no_variant_attributes_price_extra=tuple(no_variant_attributes_price_extra)
            )

        if self.order_id.pricelist_id.discount_policy == 'with_discount':
            return product.with_context(pricelist=self.order_id.pricelist_id.id).price
        product_context = dict(self.env.context, partner_id=self.order_id.partner_id.id, date=self.order_id.date_order,
                               uom=self.product_uom.id)

        final_price, rule_id = self.order_id.pricelist_id.with_context(product_context).get_product_price_rule(
            self.product_id, self.product_uom_qty or 1.0, self.order_id.partner_id)
        base_price, currency = self.with_context(product_context)._get_real_price_currency(product, rule_id,
                                                                                           self.product_uom_qty,
                                                                                           self.product_uom,
                                                                                           self.order_id.pricelist_id.id)
        if currency != self.order_id.pricelist_id.currency_id:
            base_price = currency._convert(
                base_price, self.order_id.pricelist_id.currency_id,
                self.order_id.company_id or self.env.company, self.order_id.date_order or fields.Date.today())
        # negative discounts (= surcharge) are included in the display price
        return max(base_price, final_price)

    @api.onchange('product_uom', 'product_uom_qty')
    def product_uom_change(self):
        if not self.product_uom or not self.product_id:
            self.price_unit = 0.0
            return
        if self.order_id.pricelist_id and self.order_id.partner_id:
            product = self.product_id.with_context(
                lang=self.order_id.partner_id.lang,
                partner=self.order_id.partner_id,
                quantity=self.product_uom_qty,
                date=self.order_id.date_order,
                pricelist=self.order_id.pricelist_id.id,
                uom=self.product_uom.id,
                fiscal_position=self.env.context.get('fiscal_position')
            )
            self.price_unit = self.env['account.tax']._fix_tax_included_price_company(self._get_display_price(product),
                                                                                      product.taxes_id, self.tax_id,
                                                                                      self.company_id)

    def _compute_tax_id(self):
        for line in self:
            fpos = line.order_id.fiscal_position_id or line.order_id.partner_id.property_account_position_id
            # If company_id is set, always filter taxes by the company
            taxes = line.product_id.taxes_id.filtered(lambda r: not line.company_id or r.company_id == line.company_id)
            line.tax_id = fpos.map_tax(taxes, line.product_id, line.order_id.partner_shipping_id) if fpos else taxes

    def get_sale_order_line_multiline_description_sale(self, product):
        """ Compute a default multiline description for this sales order line.

        In most cases the product description is enough but sometimes we need to append information that only
        exists on the sale order line itself.
        e.g:
        - custom attributes and attributes that don't create variants, both introduced by the "product configurator"
        - in event_sale we need to know specifically the sales order line as well as the product to generate the name:
          the product is not sufficient because we also need to know the event_id and the event_ticket_id (both which belong to the sale order line).
        """
        return product.get_product_multiline_description_sale() + self._get_sale_order_line_multiline_description_variants()

    def _get_sale_order_line_multiline_description_variants(self):
        """When using no_variant attributes or is_custom values, the product
        itself is not sufficient to create the description: we need to add
        information about those special attributes and values.

        :return: the description related to special variant attributes/values
        :rtype: string
        """
        if not self.product_custom_attribute_value_ids and not self.product_no_variant_attribute_value_ids:
            return ""

        name = "\n"

        custom_ptavs = self.product_custom_attribute_value_ids.custom_product_template_attribute_value_id
        no_variant_ptavs = self.product_no_variant_attribute_value_ids._origin

        # display the no_variant attributes, except those that are also
        # displayed by a custom (avoid duplicate description)
        for ptav in (no_variant_ptavs - custom_ptavs):
            name += "\n" + ptav.display_name

        # display the is_custom values
        for pacv in self.product_custom_attribute_value_ids:
            name += "\n" + pacv.display_name

        return name

    def validate_fields(self):
        try:
            for line in self:
                assert line.price_unit > 0.0, _("Price must be greater than zero")
                assert line.product_uom_qty > 0.0, _("Quantity must be greater than zero")
        except AssertionError as e:
            raise UserError(e)

    def _get_real_price_currency(self, product, rule_id, qty, uom, pricelist_id):
        """Retrieve the price before applying the pricelist
            :param obj product: object of current product record
            :parem float qty: total quentity of product
            :param tuple price_and_rule: tuple(price, suitable_rule) coming from pricelist computation
            :param obj uom: unit of measure of current order line
            :param integer pricelist_id: pricelist id of sales order"""
        PricelistItem = self.env['product.pricelist.item']
        field_name = 'lst_price'
        currency_id = None
        product_currency = None
        if rule_id:
            pricelist_item = PricelistItem.browse(rule_id)
            if pricelist_item.pricelist_id.discount_policy == 'without_discount':
                while pricelist_item.base == 'pricelist' and pricelist_item.base_pricelist_id and pricelist_item.base_pricelist_id.discount_policy == 'without_discount':
                    price, rule_id = pricelist_item.base_pricelist_id.with_context(uom=uom.id).get_product_price_rule(
                        product, qty, self.order_id.partner_id)
                    pricelist_item = PricelistItem.browse(rule_id)

            if pricelist_item.base == 'standard_price':
                field_name = 'standard_price'
            if pricelist_item.base == 'pricelist' and pricelist_item.base_pricelist_id:
                field_name = 'price'
                product = product.with_context(pricelist=pricelist_item.base_pricelist_id.id)
                product_currency = pricelist_item.base_pricelist_id.currency_id
            currency_id = pricelist_item.pricelist_id.currency_id

        product_currency = product_currency or (
                product.company_id and product.company_id.currency_id) or self.env.company.currency_id
        if not currency_id:
            currency_id = product_currency
            cur_factor = 1.0
        else:
            if currency_id.id == product_currency.id:
                cur_factor = 1.0
            else:
                cur_factor = currency_id._get_conversion_rate(product_currency, currency_id,
                                                              self.company_id or self.env.company,
                                                              self.order_id.date_order or fields.Date.today())

        product_uom = self.env.context.get('uom') or product.uom_id.id
        if uom and uom.id != product_uom:
            # the unit price is in a different uom
            uom_factor = uom._compute_price(1.0, product.uom_id)
        else:
            uom_factor = 1.0

        return product[field_name] * uom_factor * cur_factor, currency_id

    @api.onchange('product_id', 'price_unit', 'product_uom', 'product_uom_qty', 'tax_id')
    def _onchange_discount(self):
        if not (self.product_id and self.product_uom and
                self.order_id.partner_id and self.order_id.pricelist_id and
                self.order_id.pricelist_id.discount_policy == 'without_discount' and
                self.env.user.has_group('product.group_discount_per_so_line')):
            return

        self.discount = 0.0
        product = self.product_id.with_context(
            lang=self.order_id.partner_id.lang,
            partner=self.order_id.partner_id,
            quantity=self.product_uom_qty,
            date=self.order_id.date_order,
            pricelist=self.order_id.pricelist_id.id,
            uom=self.product_uom.id,
            fiscal_position=self.env.context.get('fiscal_position')
        )
        product_context = dict(self.env.context, partner_id=self.order_id.partner_id.id, date=self.order_id.date_order,
                               uom=self.product_uom.id)

        price, rule_id = self.order_id.pricelist_id.with_context(product_context).get_product_price_rule(
            self.product_id, self.product_uom_qty or 1.0, self.order_id.partner_id)
        new_list_price, currency = self.with_context(product_context)._get_real_price_currency(product, rule_id,
                                                                                               self.product_uom_qty,
                                                                                               self.product_uom,
                                                                                               self.order_id.pricelist_id.id)

        if new_list_price != 0:
            if self.order_id.pricelist_id.currency_id != currency:
                # we need new_list_price in the same currency as price, which is in the SO's pricelist's currency
                new_list_price = currency._convert(
                    new_list_price, self.order_id.pricelist_id.currency_id,
                    self.order_id.company_id or self.env.company, self.order_id.date_order or fields.Date.today())
            discount = (new_list_price - price) / new_list_price * 100
            if (discount > 0 and new_list_price > 0) or (discount < 0 and new_list_price < 0):
                self.discount = discount


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    blanket_order_id = fields.Many2one(
        'sale.blanket.order', string='Origin blanket order',
        related='order_line.blanket_order_line.order_id')

    @api.model
    def check_consumed_blanket_order_line(self):
        return any(line.blanket_order_line.remaining_qty < 0.0 for line in self.order_line)

    def button_confirm(self):
        res = super().button_confirm()
        for order in self:
            if order.check_consumed_blanket_order_line():
                raise ValidationError(
                    _('Cannot confirm order %s as one of the lines refers '
                      'to a blanket order that has no remaining quantity.') % order.name)
        return res

    @api.constrains('partner_id')
    def check_partner_id(self):
        for line in self.order_line:
            if line.blanket_order_line:
                if line.blanket_order_line.partner_id != self.partner_id:
                    raise ValidationError(
                        _("The customer must be same to the blanket order line's customer"))


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    blanket_order_line = fields.Many2one('sale.blanket.order.line', string='Blanket Order line', copy=False)

    def assigned_bo_line(self, bo_lines):
        assigned_bo_line = False
        date_planned = datetime.date.today()
        date_delta = datetime.timedelta(days=365)

        for line in bo_lines.filtered(lambda l: l.date_scheduled):
            date_scheduled = line.date_scheduled
            if date_scheduled and abs(date_scheduled - date_planned) < date_delta:
                assigned_bo_line = line
                date_delta = abs(date_scheduled - date_planned)
        if assigned_bo_line:
            return assigned_bo_line
        non_date_bo_lines = bo_lines.filtered(lambda l: not l.date_scheduled)
        if non_date_bo_lines:
            return non_date_bo_lines[0]

    def _get_eligible_bo_lines_domain(self, base_qty):
        filters = [('product_id', '=', self.product_id.id),
                   ('remaining_qty', '>=', base_qty),
                   ('currency_id', '=', self.order_id.currency_id.id),
                   ('order_id.state', '=', 'open')]
        if self.order_id.partner_id:
            filters.append(('partner_id', '=', self.order_id.partner_id.id))
        return filters

    def _get_eligible_bo_lines(self):
        base_qty = self.product_uom._compute_quantity(
            self.product_uom_qty, self.product_id.uom_id)
        filters = self._get_eligible_bo_lines_domain(base_qty)
        return self.env['sale.blanket.order.line'].search(filters)

    def get_assigned_bo_line(self):
        self.ensure_one()
        eligible_bo_lines = self._get_eligible_bo_lines()
        if eligible_bo_lines:
            if not self.blanket_order_line or self.blanket_order_line not in eligible_bo_lines:
                self.blanket_order_line = self.assigned_bo_line(eligible_bo_lines)
        else:
            self.blanket_order_line = False
        self.onchange_blanket_order_line()
        return {'domain': {'blanket_order_line': [('id', 'in', eligible_bo_lines.ids)]}}

    @api.onchange('product_id', 'order_partner_id')
    def onchange_product_id(self):
        if self.product_id:
            return self.get_assigned_bo_line()
        return

    @api.onchange('product_uom_qty', 'product_uom')
    def product_uom_change(self):
        res = super().product_uom_change()
        if self.product_id and not self.env.context.get('skip_blanket_find', False):
            return self.get_assigned_bo_line()
        return res

    @api.onchange('blanket_order_line')
    def onchange_blanket_order_line(self):
        blanket_order_line = self.blanket_order_line
        if blanket_order_line:
            self.product_id = blanket_order_line.product_id
            if blanket_order_line.product_uom != self.product_uom:
                price_unit = blanket_order_line.product_uom._compute_price(blanket_order_line.price_unit,
                                                                           self.product_uom)
            else:
                price_unit = blanket_order_line.price_unit
            self.price_unit = price_unit
            if blanket_order_line.tax_id:
                self.tax_id = blanket_order_line.tax_id
        else:
            self._compute_tax_id()
            self.with_context(skip_blanket_find=True).product_uom_change()

    @api.constrains('product_id')
    def check_product_id(self):
        for line in self:
            if line.blanket_order_line and line.product_id != line.blanket_order_line.product_id:
                raise ValidationError(_('The product in the blanket order and in the sales order must be same'))

    @api.constrains('currency_id')
    def check_currency(self):
        for line in self:
            if line.blanket_order_line:
                if line.currency_id != line.blanket_order_line.order_id.currency_id:
                    raise ValidationError(_('The currency of the blanket order must be same that of the sale order.'))
