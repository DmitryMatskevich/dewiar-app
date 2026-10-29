"""Native Contacts adapter. Never requests access implicitly or reads notes/photos."""
import sys
import threading
import time


def permission():
    if sys.platform!='darwin':return {'state':'unavailable','granted':False,'limited':False}
    from Contacts import CNContactStore,CNEntityTypeContacts
    code=int(CNContactStore.authorizationStatusForEntityType_(CNEntityTypeContacts))
    return {'state':{0:'not_determined',1:'restricted',2:'denied',3:'authorized',4:'limited'}.get(code,'unknown'),
            'granted':code in (3,4),'limited':code==4}


class MacContacts:
    def status(self):return permission()

    def fetch(self,query=None,identifiers=None):
        import os
        if os.environ.get('DEWIAR_EXAM_LAUNCH') or os.environ.get('DEWIAR_DRY_TOOLS'):return []
        if not permission()['granted']:raise PermissionError('Разрешите Dewiar доступ к Контактам macOS в системных настройках.')
        import Contacts as C
        keys=[C.CNContactIdentifierKey,C.CNContactGivenNameKey,C.CNContactMiddleNameKey,C.CNContactFamilyNameKey,
              C.CNContactOrganizationNameKey,C.CNContactPhoneNumbersKey,C.CNContactEmailAddressesKey,C.CNContactBirthdayKey]
        request=C.CNContactFetchRequest.alloc().initWithKeysToFetch_(keys)
        request.setUnifyResults_(True)
        if identifiers is not None:request.setPredicate_(C.CNContact.predicateForContactsWithIdentifiers_(identifiers))
        elif query:request.setPredicate_(C.CNContact.predicateForContactsMatchingName_(query))
        rows=[];overflow=[]
        def receive(contact,stop):
            if len(rows)>=20000:
                overflow.append(True);return
            birth=contact.birthday();birthday=None
            if birth and 1<=int(birth.month())<=12 and 1<=int(birth.day())<=31:
                birthday={'month':int(birth.month()),'day':int(birth.day())}
                if 1<=int(birth.year())<=9999:birthday['year']=int(birth.year())
                import datetime
                try:datetime.date(birthday.get('year',2000),birthday['month'],birthday['day'])
                except ValueError:birthday=None
            rows.append({'source_id':str(contact.identifier()),'given_name':str(contact.givenName() or ''),
                         'middle_name':str(contact.middleName() or ''),'family_name':str(contact.familyName() or ''),
                         'organization':str(contact.organizationName() or ''),
                         'phones':[str(x.value().stringValue()) for x in contact.phoneNumbers()],
                         'emails':[str(x.value()) for x in contact.emailAddresses()],'birthday':birthday})
        result,error=C.CNContactStore.alloc().init().enumerateContactsWithFetchRequest_error_usingBlock_(request,None,receive)
        if not result:raise ValueError('Не удалось прочитать Контакты macOS. Проверьте разрешение и повторите.')
        if overflow:raise ValueError('Доступно больше 20 000 карточек. Импорт остановлен, данные не изменены.')
        return rows

    def create(self,row):
        import os
        if os.environ.get('DEWIAR_EXAM_LAUNCH') or os.environ.get('DEWIAR_DRY_TOOLS'):raise ValueError('В тестовом режиме системная адресная книга не изменяется')
        if not permission()['granted']:raise PermissionError('Доступ к Контактам macOS не разрешён.')
        import Contacts as C
        contact=build_contact(row)
        request=C.CNSaveRequest.alloc().init();request.addContact_toContainerWithIdentifier_(contact,None)
        ok,error=C.CNContactStore.alloc().init().executeSaveRequest_error_(request,None)
        if not ok:raise ValueError('macOS не подтвердила создание контакта. Проверьте Контакты перед повтором.')
        return str(contact.identifier())


def build_contact(row):
    import Contacts as C
    contact=C.CNMutableContact.alloc().init()
    contact.setGivenName_(row['given_name']);contact.setFamilyName_(row['family_name']);contact.setMiddleName_(row['middle_name'])
    contact.setOrganizationName_(row['organization'])
    contact.setPhoneNumbers_([C.CNLabeledValue.labeledValueWithLabel_value_(C.CNLabelPhoneNumberMain,C.CNPhoneNumber.phoneNumberWithStringValue_(x)) for x in row['phones']])
    contact.setEmailAddresses_([C.CNLabeledValue.labeledValueWithLabel_value_(C.CNLabelHome,x) for x in row['emails']])
    if row.get('birthday'):
        from Foundation import NSDateComponents
        b=row['birthday'];date=NSDateComponents.alloc().init();date.setMonth_(b['month']);date.setDay_(b['day'])
        if b.get('year'):date.setYear_(b['year'])
        contact.setBirthday_(date)
    return contact
