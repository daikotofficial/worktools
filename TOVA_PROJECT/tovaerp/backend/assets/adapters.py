from allauth.account.adapter import DefaultAccountAdapter
from allauth.account.utils import user_pk_to_url_str

class CustomAccountAdapter(DefaultAccountAdapter):
    def send_confirmation_mail(self, request, emailconfirmation, signup):
        user = emailconfirmation.email_address.user
        email = emailconfirmation.email_address.email
        ctx = {
            'emailconfirmation': emailconfirmation,
            'request': request,
            'email': email,
            'uid': user_pk_to_url_str(user),
            'token': emailconfirmation.key,
            'user': user,
            'protocol': 'https' if request.is_secure() else 'http',
            'domain': request.get_host(),
        }
        self.send_mail('account/email/email_confirmation', email, ctx)


