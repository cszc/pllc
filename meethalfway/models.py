from django.db import models
import requests
import json
import time
import re
import googlemaps
import csv
from random_words import RandomWords
import pyusps_modified

with open('apikeys.txt', 'r') as f:
    APIKEY = f.readline().strip()

GMAPS = googlemaps.Client(key=APIKEY)

class Address(models.Model):
    street = models.CharField(max_length = 64)
    city = models.CharField(max_length = 64)
    state = models.CharField(max_length = 2)
    zip_code = models.CharField(max_length = 5)

    def verify_address(self):
        verify = True
        address = ""
        suggestion = ""
        with open('uspskey.txt', 'r') as f:
            uspskey = f.readline().strip()
        addr = dict([
            ('address', self.street),
            ('city', self.city),
            ('state', self.state),
            ('zip_code', self.zip_code),
            ])
        try:
            address = pyusps_modified.verify(uspskey,addr)

        except ValueError as e:
            verify = False
            if "-2147219402" in str(e):
                suggestion = "Check your state field entry"
            if "-2147219403" in str(e):
                suggestion = "This address matches more than one address. Please use a different address."
            if "-2147219401" in str(e):
                suggestion = "No address found at this location."
            if  "-2147219400" in str(e):
                suggestion = "Check your city field entry"


        return verify, suggestion, address

    def __str__(self):
        return "%s %s %s %s" % (self.street, self.city, self.state, self.zip_code)

class Participant(models.Model):
    TRANSIT_TYPES = (
        ("walking", "Walking"),
        ("transit", "Public Transit"),
        ("driving", "Driving"),
        ("bicycling", "Bicycling")
        )
    starting_location = models.ForeignKey(Address, null=True, blank = True)
    transit_mode = models.CharField(max_length = 70, choices = TRANSIT_TYPES)

    def get_id(self):
        return self.id

class Destination(models.Model):
    address = models.CharField(max_length = 100, null=True, blank = True)
    a_time = models.CharField(max_length = 3, null=True, blank = True)
    b_time = models.CharField(max_length = 3, null=True, blank = True)
    latlng = models.CharField(max_length = 64, null=True, blank = True)
    name = models.CharField(max_length = 64, null=True, blank = True)
    place_id = models.CharField(max_length = 64, null=True, blank = True)
    score = models.CharField(max_length = 3, null=True, blank = True)
    avg_time = models.CharField(max_length = 3, null=True, blank = True)

class Meeting(models.Model):
    BUSINESS_TYPES = (
        ("cafe", "Cafe"),
        ("bar", "Bar"),
        ("restaurant", "Restaurant")
        )
    participant_one = models.ForeignKey(
        Participant, related_name = 'participant_one', null = True, blank =  True)
    participant_two = models.ForeignKey(
        Participant, related_name = 'participant_two', null = True, blank = True)
    business_type = models.CharField(
        max_length=64, null=True, blank=True, choices = BUSINESS_TYPES)
    trip_id = models.CharField(
        max_length = 100, null=True, blank = True)
    destinations = models.ManyToManyField(
        Destination, blank = True)
    share_location = models.BooleanField(default = False)

    def set_participant_two(self, participant):
        self.participant_two = participant

    def get_id(self):
        return self.id

    def random_words(self):
        #for url creation
        rw =  RandomWords()
        w1 = rw.random_word()
        w2 = rw.random_word()
        w3 = rw.random_word()
        return w1 + "-" + w2 + "-" + w3

    def get_destinations(self):
        address1 = str(self.participant_one.starting_location)
        address2 = str(self.participant_two.starting_location)

        mode1 = self.participant_one.transit_mode
        mode2 = self.participant_two.transit_mode

        #Step 1: Get potential destinations based on midpoint for each participant
        #returns a dict and total time
        potential_destinations = self.try_step_one(
            address1, address2, mode1, mode2)

        #Step 2: Get the times from each participant to each potential destination
        found_result, rv = self.try_step_two(
            potential_destinations, address1, address2, mode1, mode2)

        #Step 3: If good results found, create and add destination objects.
        #Otherwise, return None and try again. What is RV here again? Note to rename
        if found_result:
            self.try_step_three(rv, potential_destinations)
        else:
            new_midpoint = rv
            potential_destinations2 = get_potential_destinations(midpoint = new_midpoint)
            found_result2, rv2 = self.try_step_two(potential_destinations2)
            if found_result2:
                self.try_step_three(rv2, potential_destinations2)
            else:
                return None


    def get_target_time(self, time_a, time_b):
        total_time = time_a + time_b
        target_time = (time_a / total_time) * time_b
        return target_time


    def try_step_one(self, address1, address2, mode1, mode2):
        directions_a = self.get_directions(address1, address2, mode=mode1)
        directions_b = self.get_directions(address2, address1, mode=mode2)

        steps_a, time_a = self.get_steps_and_time(directions_a)
        steps_b, time_b = self.get_steps_and_time(directions_b)

        target_time = self.get_target_time(time_a, time_b)

        potential_dest_a = self.get_potential_destinations(
            steps = steps_a, time = target_time)
        potential_dest_b = self.get_potential_destinations(
            steps = steps_b, time = target_time)

        return dict(potential_dest_a, **potential_dest_b)


    def try_step_two(self, potential_dest, address1, address2, mode1, mode2):
        to_try = []
        for k, v in potential_dest.items():
            if len(to_try) < 20:
                to_try.append(v['address'])

        matrix_a = self.get_matrix(address1, to_try, mode1)
        matrix_b = self.get_matrix(address2, to_try, mode2)

        return self.get_results(matrix_a, matrix_b)


    def try_step_three(self, rv, potential_dest):
        final = self.map_addresses(rv, potential_dest)
        for d, v in final.items():
            dest = Destination.objects.create(
                address = d, a_time = v['a_mins'],
                b_time = v['b_mins'],
                latlng = v['latlng'],
                name = v['name'],
                place_id = v['place_id'],
                score = round(v['score']),
                avg_time = round((v['a_mins'] + v['b_mins']) / 2))
            dest.save()
            self.destinations.add(dest)


    def get_potential_destinations(self, steps=None, time=None, midpoint = None):
        '''
        Returns a dictionary of potential destinations (dicts)
        '''
        if not midpoint:
            midpoint = self.get_midpoint(steps, time)
        places_dict = {
            'key': APIKEY,
            'location': midpoint,
            'rankby': 'distance',
            'types': self.business_type}
        dest_dict = self.get_places(places_dict)
        return dest_dict


    def map_addresses(self, results, dests):
        keys = {}
        for address in results.keys():
            short_ad = re.search('\w+[\w\s]+,', address)
            pat = "^" + short_ad.group()
            for k, v in dests.items():
                match = re.search(pat, v['address'])
                if match:
                    if address not in keys:
                        keys[address] = k
        final_rv = {}
        for k, v in keys.items():
            final_rv[k] = {"latlng": v,
                "name": dests[v]['name'],
                'place_id': dests[v]['place_id'],
                'a_mins': results[k]['a_mins'],
                'b_mins': results[k]['b_mins'],
                'score': 100 - (results[k]['score'] * 100)}
        return final_rv


    def bisect(self, target_time, current_time, step):
        '''
        Given a target time, current time, and one step from a call to
        Google Directions, returns a lat long as a string for the desired
        location along the path.
        '''
        time_left = target_time - current_time
        duration = step['duration']['value']
        start_lat = step['start_location']['lat']
        start_lng = step['start_location']['lng']
        end_lat = step['end_location']['lat']
        end_lng = step['end_location']['lng']
        ratio = time_left / duration

        add_lat = ratio*(end_lat - start_lat)
        add_lng = ratio*(end_lng - start_lng)
        new_lat = start_lat + add_lat
        new_lng = start_lng + add_lng
        s = str(new_lat) + "," + str(new_lng)
        return s


    def get_directions(self, origin, destination, mode='transit'):
        return GMAP.directions(origin, destination, mode=mode)


    def get_steps_and_time(self, directions):
        legs = directions[0]['legs']
        time = legs[0]['duration']['value']
        steps = legs[0]['steps']
        substeps = self.get_substeps(steps)
        return substeps, time


    def get_substeps(self, steps):
        substeps = []
        for x in steps:
            if 'steps' in x.keys():
                for substep in x['steps']:
                    substeps.append(substep)
            else:
                substeps.append(x)
        return substeps


    def get_midpoint(self, steps, target_time):
        current_time = 0
        for step in steps:
            duration = step['duration']['value']
            end_time = current_time + duration
            if end_time < target_time:
                current_time = end_time
                continue
            return self.bisect(target_time, current_time, step)


    def get_places(self, args):
        # use Requests instead of googlemaps package here because package requires
        # a query string, which we don't want
        r = requests.get(
            "https://maps.googleapis.com/maps/api/place/nearbysearch/json?",
            params = args)
        data = r.json()
        dest_dict = self.parse_places(data)
        return dest_dict


    def parse_places(self, places):
        dest_dict = {}
        for p in places["results"]:
            lat = p['geometry']['location']['lat']
            lng = p['geometry']['location']['lng']
            name = p['name']
            place_id = p['place_id']
            coords = str(lat) + "," + str(lng)
            r = GMAP.place(place_id)
            address = r['result']['formatted_address']
            dest_dict[coords] = {'name': name, 'place_id': place_id, 'address': address}
        return dest_dict


    def get_matrix(self, origins, destinations, mode='transit'):
        matrix = GMAP.distance_matrix(origins, destinations, mode=mode)
        return matrix


    def get_results(self, matrix_a, matrix_b):
        ADDRESS = 0
        SCORE = 1
        scores = {}
        addresses = matrix_a['destination_addresses']
        a_times = matrix_a['rows'][0]['elements']
        b_times = matrix_b['rows'][0]['elements']
        best = (None, 0)

        for i, address_i in enumerate(addresses):
            a_time = a_times[i]['duration']['value']
            b_time = b_times[i]['duration']['value']
            if a_time <= b_time:
                this_score = 1 - (a_time/b_time)
            else:
                this_score = 1 - (b_time/a_time)
            scores[address_i] = {
                'a_mins': a_time /60,
                'b_mins': b_time/60,
                'score': this_score}
            if this_score > best[SCORE]:
                best = (address_i, this_score)

        return_values = {}
        for k, v in scores.items():
            if len(return_values) < 5:
                if v['score'] < 0.2:
                    return_values[k] = v
        if len(return_values) == 0:
            found_result = False
            return found_result, best[ADDRESS]
        else:
            found_result = True
            return found_result, return_values

    def __str__(self):
        return "%s " % (self.trip_id)
