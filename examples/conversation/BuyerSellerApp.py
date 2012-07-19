from gevent import spawn
from gevent import queue as gqueue
from pyon.net.transport import NameTrio
from pyon.net import channel
from pyon.net import messaging
from pyon.net import conversation
from pyon.net.conversation import Conversation, Principal,InitiatorPrincipal, GuestPrincipal

node, ioloop_process = messaging.make_node()
def buyer_app(queue_name):
    #principal initialisation
    customer = Principal(node, NameTrio('rumi-PC',
                                        'rumi'))
    # conversation bootstrapping
    c = customer.start_conversation(protocol = 'buyer_seller_protocol',
                                    role = 'buyer')

    c.invite('seller', NameTrio('stephen-PC',service_provider_name),
                       merge_with_first_send = True)

    #interactions
    c.send('seller', 'I will send you a request shortly. Please wait for me.')
    c.send('seller', 'How expensive is War and Piece?')
    msg, header = c.recv('seller')
    print 'Msg received: %s' % (msg)

    #cleaning
    customer.stop_conversation()

def seller_app(service_provider_name):
    service_provider = Principal(node, NameTrio('stephen-PC',
                                                service_provider_name))
    service_provider.start_listening()
    c = service_provider.accept_next_invitation(merge_with_first_send = True)


    #interactions
    msg, header = c.recv('buyer')
    print 'Msg received: %s' %(msg)
    msg, header = c.recv('buyer')
    print 'Msg received: %s' %(msg)
    c.send('buyer', '3000 pounds')

    c.close()
    local.terminate()